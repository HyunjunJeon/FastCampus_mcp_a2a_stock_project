"""
A2A 통합이 적용된 데이터 수집(DataCollector) 에이전트 V2.

이 모듈은 표준 A2A 인터페이스를 구현하여 A2A 프로토콜과 매끄럽게 연동되는
데이터 수집 에이전트를 제공합니다.

메모:
      너무 작은 토큰 조각은 소음을 줄이기 위해 버퍼링됩니다.
    - 최종 출력에는 도구 호출 횟수와 수집된 종목 수 등의 카운트 정보가 포함되어,
      감독 에이전트가 자유 텍스트를 파싱하지 않고도 진행 상황을 요약할 수 있습니다.
"""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

import pytz
import structlog
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from src.lg_agents.base.a2a_interface import A2AOutput, A2AStreamBuffer, BaseA2AAgent
from src.lg_agents.base.mcp_config import load_data_collector_tools
from src.lg_agents.prompts import get_prompt
from src.lg_agents.util import load_env_file

logger = structlog.get_logger(__name__)

load_env_file()


class DataCollectorA2AAgent(BaseA2AAgent):
    """
    A2A 통합을 지원하는 데이터 수집 에이전트.

    이 에이전트는 다양한 소스(시세, 뉴스 등)에서 데이터를 수집하고,
    스트리밍/폴링 작업 모두에 대해 표준화된 A2A 출력을 제공합니다.
    """

    def __init__(self, model=None, is_debug: bool = False, checkpointer=None):
        """
        데이터 수집 A2A 에이전트 초기화.

        Args:
            model: 사용할 LLM 모델 (기본: gpt-4.1)
            is_debug: 디버그 모드 여부
            checkpointer: 체크포인트 매니저 (기본: MemorySaver)
        """
        BaseA2AAgent.__init__(self)

        self.is_debug = is_debug
        self.model = model or init_chat_model(
            model="gpt-4.1", temperature=0, model_provider="openai"
        )
        self.checkpointer = checkpointer or MemorySaver()

        self.tools = []

        # Stream buffer for managing LLM output
        self.stream_buffer = A2AStreamBuffer(max_size=200)

        # Track collected data during execution
        self.collected_symbols = set()
        self.tool_calls_count = 0

    async def initialize(self):
        """MCP 도구 로딩 및 그래프 생성 초기화.

        단계:
            1) 데이터 수집에 특화된 MCP 도구 로딩
            2) 목적에 맞춘 시스템 프롬프트 생성
            3) 체크포인트가 포함된 ReAct 그래프 구성

        Raises:
            RuntimeError: 도구/프롬프트/그래프 설정 중 발생한 하위 오류를 래핑하여 전달
        """
        try:
            # Load MCP tools
            self.tools = await load_data_collector_tools()
            logger.info(f" Loaded {len(self.tools)} MCP tools for DataCollector")

            # Get system prompt
            system_prompt = get_prompt(
                "data_collector", "system", tool_count=len(self.tools)
            )

            # Create the reactive agent graph
            self.graph = create_react_agent(
                model=self.model,
                tools=self.tools,
                prompt=system_prompt,
                checkpointer=self.checkpointer,
                name="DataCollectorAgent",
                debug=self.is_debug,
            )

            logger.info(" DataCollector A2A Agent initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize DataCollector Agent: {e}")
            raise RuntimeError(f"DataCollector Agent initialization failed: {e}") from e

    async def execute_for_a2a(
        self, input_dict: Dict[str, Any], config: Optional[Dict[str, Any]] = None
    ) -> A2AOutput:
        """
        A2A 호환 입력/출력으로 에이전트를 실행합니다.

        Args:
            input_dict: ``{"messages": [...]}`` 형태의 페이로드 또는 구조화된 수집 요청.
                A2A DataPart 형식도 자동으로 변환하여 처리합니다.
            config: 선택적 실행 설정. 제공되지 않으면 기본 ``thread_id`` 가 설정됩니다.

        Returns:
            A2AOutput: A2A 처리를 위한 표준화된 출력
        """
        if not self.graph:
            await self.initialize()

        try:
            # 입력 형식 정규화 (A2A DataPart → LangGraph 메시지 형식)
            normalized_input = self._normalize_input(input_dict)

            # Reset tracking variables
            self.collected_symbols.clear()
            self.tool_calls_count = 0

            # Execute the graph with normalized input
            config_to_use = config
            if config_to_use is None or isinstance(config_to_use, dict):
                config_to_use = RunnableConfig(configurable={"thread_id": str(uuid4())})
            result = await self.graph.ainvoke(normalized_input, config=config_to_use)

            logger.info(f"[DataCollectorA2AAgent] Result: {result}")

            # Extract final output
            return self.extract_final_output(result)

        except Exception as e:
            return self.format_error(e, context="execute_for_a2a")

    def format_stream_event(self, event: Dict[str, Any]) -> Optional[A2AOutput]:
        """LangGraph 스트리밍 이벤트를 ``A2AOutput`` 업데이트로 변환합니다.

        너무 작은 토큰 조각으로 버퍼에 남아 있는 경우에는 ``None`` 을 반환합니다.
        """
        event_type = event.get("event", "")

        # Handle LLM streaming
        if event_type == "on_llm_stream":
            content = self.extract_llm_content(event)
            if content and self.stream_buffer.add(content):
                # Buffer is full, flush it
                return self.create_a2a_output(
                    status="working",
                    text_content=self.stream_buffer.flush(),
                    stream_event=True,
                    metadata={"event_type": "llm_stream"},
                )

        # Handle tool execution events
        elif event_type == "on_tool_start":
            tool_name = event.get("name", "unknown")
            self.tool_calls_count += 1

            # Extract symbols from tool inputs if available
            if "data" in event and "input" in event["data"]:
                tool_input = event["data"]["input"]
                if isinstance(tool_input, dict) and "symbol" in tool_input:
                    self.collected_symbols.add(tool_input["symbol"])

            return self.create_a2a_output(
                status="working",
                text_content=f" 데이터 수집 중: {tool_name}",
                stream_event=True,
                metadata={
                    "event_type": "tool_start",
                    "tool_name": tool_name,
                    "tool_call_number": self.tool_calls_count,
                },
            )

        # Handle tool completion
        elif event_type == "on_tool_end":
            tool_output = event.get("data", {}).get("output", {})

            # 의미 있는 도구 출력에 대해서만 구조화된 데이터를 전송
            if tool_output and isinstance(tool_output, dict):
                return self.create_a2a_output(
                    status="working",
                    data_content={
                        "tool_result": tool_output,
                        "symbols_collected": list(self.collected_symbols),
                        "tool_calls_made": self.tool_calls_count,
                    },
                    stream_event=True,
                    metadata={"event_type": "tool_end"},
                )

        # Handle completion events
        elif self.is_completion_event(event):
            # Flush any remaining buffer content
            if self.stream_buffer.has_content():
                return self.create_a2a_output(
                    status="working",
                    text_content=self.stream_buffer.flush(),
                    stream_event=True,
                    metadata={"event_type": "buffer_flush"},
                )

        return None

    def extract_final_output(self, state: Dict[str, Any]) -> A2AOutput:
        """LangGraph 실행 상태로부터 최종 ``A2AOutput`` 을 생성합니다.

        후속 처리를 쉽게 하기 위해 카운터 및 타임스탬프를 포함합니다.
        """
        try:
            # Extract messages from state
            messages = state.get("messages", [])

            # Get the last AI message as summary
            summary = ""
            for msg in reversed(messages):
                if hasattr(msg, "content") and msg.__class__.__name__ == "AIMessage":
                    summary = msg.content
                    break

            # Count total messages
            total_messages = len(messages)

            # Prepare structured data
            data_content = {
                "success": True,
                "result": {
                    "raw_response": summary,
                    "symbols_collected": list(self.collected_symbols),
                    "tool_calls_made": self.tool_calls_count,
                    "total_messages_count": total_messages,
                    "timestamp": datetime.now(pytz.UTC).isoformat(),
                },
                "agent_type": "DataCollectorA2AAgent",
                "workflow_status": "completed",
            }

            # Create final output
            return self.create_a2a_output(
                status="completed",
                text_content=summary or "데이터 수집이 완료되었습니다.",
                data_content=data_content,
                final=True,
                metadata={
                    "execution_complete": True,
                    "symbols_count": len(self.collected_symbols),
                    "tool_calls_count": self.tool_calls_count,
                },
            )

        except Exception as e:
            logger.error(f"Error extracting final output: {e}")
            return self.format_error(e, context="extract_final_output")

    # Helper methods for data collection

    def _normalize_input(self, input_dict: Dict[str, Any]) -> Dict[str, Any]:
        """A2A DataPart 형식을 LangGraph 메시지 형식으로 변환합니다.

        A2A 프로토콜에서 전송되는 구조화된 데이터(DataPart)를 LangGraph가
        기대하는 ``{"messages": [HumanMessage(...)]}`` 형식으로 변환합니다.

        Args:
            input_dict: A2A DataPart 또는 메시지 형식의 입력

        Returns:
            LangGraph 호환 형식의 입력 딕셔너리
        """
        # 이미 메시지 형식이면 HumanMessage로 변환하여 반환
        if "messages" in input_dict:
            messages = input_dict.get("messages", [])
            # dict 메시지를 HumanMessage로 변환
            if messages and isinstance(messages[0], dict):
                return {
                    "messages": [
                        HumanMessage(content=m.get("content", str(m)))
                        for m in messages
                    ]
                }
            # 이미 HumanMessage 객체면 그대로 반환
            return input_dict

        # DataPart 구조인 경우 변환
        symbols = input_dict.get("requested_symbols") or input_dict.get("symbols")
        data_types = input_dict.get("data_types")
        user_question = input_dict.get("user_question")

        # 기존 헬퍼 메서드 활용하여 요청 텍스트 생성
        request = self._build_collection_request(symbols, data_types, user_question)

        logger.info(f"[DataCollector] Normalized input: {request[:100]}...")

        return {"messages": [HumanMessage(content=request)]}

    async def collect_data(
        self,
        symbols: list[str] | None = None,
        data_types: list[str] | None = None,
        user_question: str | None = None,
        context_id: str | None = None,
    ) -> A2AOutput:
        """상위 수준의 데이터 수집 요청을 위한 헬퍼 메서드.

        .. deprecated::
            이 메서드는 더 이상 사용되지 않습니다. 대신 ``execute_for_a2a()``를
            직접 호출하세요. ``execute_for_a2a()``는 ``_normalize_input()``을 통해
            구조화된 입력(symbols, data_types, user_question)을 자동으로 변환합니다.

            사용 예시::

                # 기존 방식 (Deprecated)
                result = await agent.collect_data(symbols=["005930"], data_types=["price"])

                # 권장 방식
                result = await agent.execute_for_a2a({
                    "requested_symbols": ["005930"],
                    "data_types": ["price"],
                    "user_question": "삼성전자 데이터를 수집해주세요"
                })

        Args:
            symbols: 대상 종목 코드 (예: ["005930", "000660"])
            data_types: 데이터 범주 (예: ["price", "news"])
            user_question: 제공 시 자동 생성 요청 텍스트를 대체
            context_id: 재현 가능성을 위한 스레딩 컨텍스트 ID

        Returns:
            A2AOutput: 표준화된 수집 결과
        """
        import warnings
        warnings.warn(
            "collect_data()는 deprecated되었습니다. "
            "execute_for_a2a()를 직접 사용하세요.",
            DeprecationWarning,
            stacklevel=2
        )

        # Build the collection request
        request = self._build_collection_request(symbols, data_types, user_question)

        # Create input for the agent
        input_dict = {"messages": [HumanMessage(content=request)]}

        # Execute with A2A interface
        config = {"configurable": {"thread_id": context_id or "default"}}

        return await self.execute_for_a2a(input_dict, config)

    def _build_collection_request(
        self,
        symbols: list[str] | None = None,
        data_types: list[str] | None = None,
        user_question: str | None = None,
    ) -> str:
        """에이전트가 데이터를 수집하도록 간결한 한국어 지시문을 생성합니다."""
        if user_question:
            return user_question

        parts = []

        if symbols:
            parts.append(f"다음 종목들의 데이터를 수집해주세요: {', '.join(symbols)}")

        if data_types:
            type_str = ", ".join(data_types)
            parts.append(f"수집할 데이터 유형: {type_str}")
        else:
            parts.append("시세, 뉴스, 재무정보 등 관련된 모든 데이터를 수집해주세요")

        return " ".join(parts) if parts else "시장 데이터를 수집해주세요"


# Factory function for backward compatibility
async def create_data_collector_a2a_agent(
    model=None, is_debug: bool = False, checkpointer=None
) -> DataCollectorA2AAgent:
    """
    데이터 수집 A2A 에이전트를 생성하고 초기화합니다.

    Args:
        model: LLM 모델 (기본: gpt-4.1-mini)
        is_debug: 디버그 모드 플래그
        checkpointer: 체크포인트 매니저

    Returns:
        DataCollectorA2AAgent: 초기화된 에이전트 인스턴스
    """
    agent = DataCollectorA2AAgent(model, is_debug, checkpointer)
    await agent.initialize()
    return agent
