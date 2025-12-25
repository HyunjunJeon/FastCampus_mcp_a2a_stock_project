"""
LangGraph A2A 에이전트 실행기(Executor) V2.

이 모듈은 A2A 인터페이스를 갖춘 LangGraph 에이전트와 A2A 프로토콜 사이를 연동합니다.
스트리밍과 폴링 모두에서 표준화된 출력 형식을 활용합니다.
"""

from datetime import datetime
from typing import Any, AsyncGenerator, Dict, Optional, Type, cast

import pytz
import structlog
from a2a.client.helpers import create_text_message_object
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskManager, TaskUpdater
from a2a.types import (
    DataPart,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils import (
    new_agent_parts_message,
    new_agent_text_message,
)

from src.a2a_integration.models import LangGraphExecutorConfig
from src.lg_agents.base.a2a_interface import A2AOutput, BaseA2AAgent

logger = structlog.get_logger(__name__)


class LangGraphAgentExecutorV2(AgentExecutor):
    """
    A2A 인터페이스를 지원하는 LangGraph 에이전트를 위한 실행기.

    각 에이전트가 구현한 표준 A2A 인터페이스를 활용하여,
    별도의 커스텀 결과 추출기나 복잡한 스트리밍 로직 없이 동작합니다.
    """

    def __init__(
        self,
        agent_class: Type[BaseA2AAgent],
        config: Optional[LangGraphExecutorConfig] = None,
        **agent_kwargs,
    ):
        """
        LangGraph A2A Executor V2 초기화.

        Args:
            agent_class: 인스턴스화할 A2A 지원 에이전트 클래스
            config: 실행기 설정
            **agent_kwargs: 에이전트 생성자에 전달할 추가 인자
        """
        self.agent_class = agent_class
        self.agent_kwargs = agent_kwargs
        self.config = config or LangGraphExecutorConfig()
        self.agent: Optional[BaseA2AAgent] = None
        self.task_store = InMemoryTaskStore()
        self.task_manager: Optional[TaskManager] = None
        self.updater: Optional[TaskUpdater] = None
        self.event_queue: Optional[EventQueue] = None

        logger.info(f" LangGraphAgentExecutorV2 initialized for {agent_class.__name__}")

    async def _ensure_agent_initialized(self):
        """에이전트 인스턴스가 생성·초기화되었는지 보장합니다.

        - 제공된 kwargs 로 ``agent_class`` 인스턴스화
        - 에이전트에 ``initialize()`` 훅이 있으면 await 실행
        - 실패 시 ``RuntimeError`` 발생
        """
        if not self.agent:
            try:
                # Create agent instance
                self.agent = self.agent_class(**self.agent_kwargs)

                # Initialize if it has an initialize method
                if hasattr(self.agent, "initialize"):
                    await self.agent.initialize()
                    logger.info(f" Agent {self.agent.agent_type} initialized")

            except Exception as e:
                logger.error(f"Failed to initialize agent: {e}")
                raise RuntimeError(f"Agent initialization failed: {e}") from e

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """표준화된 에이전트 인터페이스로 A2A 요청을 실행합니다.

        블로킹/스트리밍 실행 모드에 따라 태스크 라이프사이클을 오케스트레이션하고,
        A2A SDK 규격의 Parts 기반 메시지를 생성합니다.

        Args:
            context: A2A 요청 컨텍스트
            event_queue: 메시지 전송을 위한 이벤트 큐
        """
        try:
            logger.info(f"Starting A2A agent execution for {self.agent_class.__name__}")

            # 에이전트 초기화 보장
            await self._ensure_agent_initialized()

            # 입력 처리
            input_dict = await self._process_input(context)
            logger.info(f"Processed input: {type(input_dict)}")

            # 태스크 업데이트 도구 준비
            task_id = cast(str, context.task_id)
            context_id = getattr(context, "context_id", task_id)
            user_message = create_text_message_object(
                content=input_dict.get("messages", [{}])[0].get("content", "")
            )

            task = context.current_task
            logger.info(f"[Execute] Task: {task}")
            if not task:
                task = Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        message=user_message,
                        state=TaskState.submitted,
                        timestamp=datetime.now(
                            tz=pytz.timezone("Asia/Seoul")
                        ).isoformat(),
                    ),
                )
                await event_queue.enqueue_event(task)
                logger.info(f"New task created and enqueued: {task_id}")
            else:
                logger.info(f"Using existing task from context: {task_id}")

            self.task_manager = TaskManager(
                task_id=task_id,
                context_id=context_id,
                task_store=self.task_store,
                initial_message=user_message,
            )
            self.updater = TaskUpdater(
                event_queue=event_queue, task_id=task_id, context_id=context_id
            )
            self.event_queue = event_queue

            is_blocking = self._is_blocking_mode(context)
            logger.info(
                f"Execution mode - blocking: {is_blocking}, "
                f"streaming enabled: {self.config.enable_streaming}"
            )

            await self.updater.update_status(TaskState.working)
            if is_blocking or not self.config.enable_streaming:
                final_message, final_status, is_final = await self._execute_blocking(
                    input_dict, context_id
                )
                logger.info(f"Blocking execution output: {final_message}")
                # 에이전트가 제공한 상태/완료 플래그를 존중하여 업데이트
                try:
                    # 에이전트 결과에서 상태와 완료 여부를 읽어서 올바르게 설정
                    mapped_state = final_status
                    await self.updater.update_status(
                        mapped_state,
                        final_message,
                        final=is_final,
                    )
                except Exception as e:
                    logger.warning(f"Failed to update task status: {e}")
            else:
                # 스트리밍 모드: 내부에서 최종 완료 시점에 스토어를 업데이트합니다
                async for _ in self._execute_streaming(input_dict, context_id):
                    pass

        except Exception as e:
            logger.error(f"Critical error in executor: {e}")
            try:
                updater = TaskUpdater(
                    event_queue=event_queue,
                    task_id=cast(str, context.task_id),
                    context_id=str(getattr(context, "context_id", context.task_id)),
                )
                await updater.update_status(
                    TaskState.failed,
                    new_agent_text_message(f"작업 중 오류가 발생했습니다: {str(e)}"),
                    final=True,
                )
            except Exception as update_error:
                logger.error(f"Failed to update error status: {update_error}")
            raise

    async def _execute_blocking(
        self,
        input_dict: Dict[str, Any],
        context_id: str,
    ) -> tuple[Message, TaskState, bool]:
        """블로킹 모드로 실행합니다(스트리밍 없음).

        에이전트의 ``execute_for_a2a`` 를 호출하고, 합쳐진 Parts 를 담은 최종
        메시지 하나를 전송합니다.

        Returns:
            tuple: (Message, TaskState, is_final)
        """
        logger.info("Using blocking execution mode")

        try:
            # 표준화된 인터페이스로 에이전트 실행
            config = {"configurable": {"thread_id": context_id}}
            result = await self.agent.execute_for_a2a(input_dict, config)

            logger.info(f"Agent execution completed, result type: {type(result)}")
            logger.info(
                f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}"
            )
            logger.info(f"Agent execution completed, status: {result.get('status')}")
            logger.info(f"Result final flag: {result.get('final', 'Not set')}")
            logger.info("===========" * 10)
            logger.info(f"Result: {result}")
            logger.info("===========" * 10)

            # 에이전트 결과에서 상태와 완료 여부 추출
            status_str = result.get("status", "working")
            final_status = self._map_status_to_task_state(status_str)
            is_final = bool(result.get("final", False))

            # A2AOutput 형식에 맞춰 결과 전송
            # NOTE: 이 안에서 상태 변경 금지.
            last_message = await self._send_a2a_output(result)
            logger.info(f"Last message: {last_message}")

            return (last_message, final_status, is_final)

        except Exception as e:
            logger.error(f"Blocking execution failed: {e}")
            raise

    async def _execute_streaming(
        self,
        input_dict: Dict[str, Any],
        context_id: str,
    ) -> AsyncGenerator[Message, None]:
        """스트리밍 지원 모드로 실행합니다.

        완료 시점까지 에이전트의 ``format_stream_event`` 를 통해 LangGraph 이벤트를
        스트리밍하고, 이후 그래프 상태에서 최종 출력을 추출합니다.
        """
        logger.info("Using streaming execution mode")

        try:
            # 스트리밍을 위해 에이전트의 그래프 이벤트에 훅을 연결해야 합니다
            # 이를 위해 에이전트에 graph 속성이 필요합니다
            if not hasattr(self.agent, "graph"):
                logger.warning(
                    "Agent doesn't support streaming, falling back to blocking"
                )
                final_message = await self._execute_blocking(input_dict, context_id)
                # 블로킹 결과로 즉시 완료 상태 전송
                if self.updater:
                    await self.updater.update_status(
                        TaskState.completed,
                        final_message,
                        final=True,
                    )
                return

            # 완료 여부/이벤트 개수 추적
            is_completed = False
            event_count = 0

            # 그래프에서 이벤트 스트리밍
            async for event in self.agent.graph.astream_events(
                input_dict, config={"configurable": {"thread_id": context_id}}
            ):
                event_count += 1

                # 에이전트로 하여금 스트리밍 이벤트를 포맷하도록 위임
                formatted_output = self.agent.format_stream_event(event)

                if formatted_output:
                    # 메시지 생성
                    _message = await self._send_a2a_output(formatted_output)
                    # 상태 매핑 및 전송
                    status_str = formatted_output.get("status", "working")
                    mapped_state = self._map_status_to_task_state(status_str)
                    is_final = bool(formatted_output.get("final", False))

                    if self.updater:
                        await self.updater.update_status(
                            mapped_state,
                            _message,
                            final=is_final,
                        )

                    yield _message

                    # 이 이벤트가 완료 신호인지 확인
                    if is_final:
                        is_completed = True
                        logger.info(" Completion detected from agent")
                        break

                # 원시 이벤트에서 완료 패턴 확인
                if not is_completed and self._is_completion_event(event):
                    is_completed = True
                    logger.info(" Completion detected from event pattern")
                    break

            # 아직 완료되지 않았다면 최종 상태를 가져오기
            if not is_completed:
                logger.info("Streaming ended, extracting final state")

                # 그래프에서 최종 상태 조회
                state_snapshot = await self.agent.graph.aget_state(
                    config={"configurable": {"thread_id": context_id}}
                )

                if state_snapshot and state_snapshot.values:
                    # 에이전트 메서드를 사용해 최종 출력 추출
                    final_output = self.agent.extract_final_output(
                        state_snapshot.values
                    )

                    # 최종 출력 전송 및 완료 처리
                    _message = await self._send_a2a_output(final_output)
                    if self.updater:
                        await self.updater.update_status(
                            TaskState.completed,
                            _message,
                            final=True,
                        )
                    yield _message

            logger.info(f" Streaming complete - Events: {event_count}")

        except Exception as e:
            logger.error(f"Streaming execution failed: {e}")
            raise

    async def _send_a2a_output(
        self,
        output: A2AOutput,
    ) -> Message:
        """
        A2AOutput 을 A2A 메시지 Parts 로 변환하여 전송합니다.

        Args:
            output: 에이전트에서 생성된 표준 A2A 출력
        """
        try:
            # A2AOutput 내용 전체 로깅
            logger.info(f"Full A2AOutput received: {output}")

            status = output.get("status", "working")
            text_content = output.get("text_content")
            data_content = output.get("data_content")
            agent_type = output.get("agent_type", "Unknown")

            logger.info(
                f"A2AOutput details: status={status}, text_content={len(text_content) if text_content else 0} chars, data_content_keys={list(data_content.keys()) if data_content else 'None'}, agent_type={agent_type}"
            )

            # Task 상태 매핑 로그
            mapped_state = self._map_status_to_task_state(status)
            logger.info(f"Task status mapping: {status} -> {mapped_state}")

            parts = []

            if text_content:
                parts.append(Part(root=TextPart(text=text_content)))
                logger.info(f"Added TextPart: {len(text_content)} chars")

            if data_content:
                parts.append(Part(root=DataPart(data=data_content)))
                logger.info(f"Added DataPart: {len(data_content)} keys")

            # 비어있는 Parts 를 방지하기 위한 폴백 처리
            if not parts:
                # 에이전트/상태 정보를 포함한 폴백 텍스트 생성
                agent_type = output.get("agent_type", "Agent")
                fallback_text = f"{agent_type} - {status}"

                # 에러 메시지가 있으면 포함
                error_msg = output.get("error_message")
                if error_msg:
                    fallback_text += f": {error_msg}"

                parts.append(Part(root=TextPart(text=fallback_text)))
                logger.warning(
                    f"No content provided, sending fallback text: {fallback_text}"
                )
                logger.warning(
                    f"A2AOutput had no valid content - text_content: {text_content is not None}, data_content: {data_content is not None}"
                )

            result = new_agent_parts_message(parts)
            logger.info(f"Created agent message with {len(parts)} parts")
            return result

        except Exception as e:
            logger.error(f"Failed to send A2A output: {e}")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """진행 중인 태스크를 취소합니다.

        Args:
            context: 요청 컨텍스트
            event_queue: 이벤트 큐
        """
        logger.info(f"Cancelling task: {context.task_id}")

        if context.current_task:
            updater = TaskUpdater(
                event_queue=event_queue,
                task_id=context.current_task.id,
                context_id=str(context.context_id),
            )
            await updater.cancel()
            logger.info(f"Task {context.task_id} cancelled")

    # Helper methods

    async def _process_input(self, context: RequestContext) -> Dict[str, Any]:
        """요청 컨텍스트에서 사용자 입력 및/또는 DataPart 페이로드를 처리합니다.

        DataPart 로부터 온 구조화된 페이로드면 그대로 반환하고,
        그렇지 않으면 최소 형태의 ``{"messages": [{"role": "user", "content": ...}]}``
        딕셔너리를 반환합니다.
        """
        query = context.get_user_input()

        # DataPart 에서 구조화된 데이터 추출 시도
        payload = {}
        if context.message and getattr(context.message, "parts", None):
            try:
                from a2a.utils import get_data_parts

                data_parts = get_data_parts(context.message.parts)
                if data_parts:
                    last_part = data_parts[-1]
                    if isinstance(last_part, dict):
                        payload = last_part
            except Exception as e:
                logger.debug(f"No DataPart found: {e}")

        # 적절한 형식으로 반환
        if payload:
            return payload
        elif query:
            return {"messages": [{"role": "user", "content": query}]}
        else:
            return {"messages": []}

    def _is_blocking_mode(self, context: RequestContext) -> bool:
        """요청 설정을 통해 블로킹 모드가 요청되었는지 확인합니다."""
        if hasattr(context, "request") and context.request:
            if (
                hasattr(context.request, "configuration")
                and context.request.configuration
            ):
                return getattr(context.request.configuration, "blocking", False)
        return False

    def _is_completion_event(self, event: Dict[str, Any]) -> bool:
        """LangGraph 이벤트가 워크플로우 완료를 의미하는지 확인합니다."""
        event_type = event.get("event", "")

        if event_type == "on_chain_end":
            node_name = event.get("name", "")
            if node_name in ["__end__", "aggregate", "complete"]:
                return True

        return False

    def _map_status_to_task_state(self, status: str) -> TaskState:
        """A2AOutput의 상태 문자열을 A2A TaskState 열거형으로 매핑합니다."""
        mapping = {
            "working": TaskState.working,
            "completed": TaskState.completed,
            "failed": TaskState.failed,
            "input_required": TaskState.input_required,
        }
        mapped_state = mapping.get(status, TaskState.working)
        logger.info(f"Status mapping - input: '{status}' -> output: {mapped_state}")
        return mapped_state


# Factory functions for creating executors for specific agents


def create_data_collector_executor(
    config: Optional[LangGraphExecutorConfig] = None, **agent_kwargs
) -> LangGraphAgentExecutorV2:
    """DataCollector 에이전트용 실행기를 생성합니다."""
    from src.a2a_agents.data_collector.data_collector_agent_a2a_v2 import (
        DataCollectorA2AAgent,
    )

    return LangGraphAgentExecutorV2(DataCollectorA2AAgent, config, **agent_kwargs)


def create_analysis_executor(
    config: Optional[LangGraphExecutorConfig] = None, **agent_kwargs
) -> LangGraphAgentExecutorV2:
    """Analysis 에이전트용 실행기를 생성합니다."""
    from src.a2a_agents.analysis.analysis_agent_a2a_v2 import AnalysisA2AAgent

    return LangGraphAgentExecutorV2(AnalysisA2AAgent, config, **agent_kwargs)


def create_trading_executor(
    config: Optional[LangGraphExecutorConfig] = None, **agent_kwargs
) -> LangGraphAgentExecutorV2:
    """Trading 에이전트용 실행기를 생성합니다."""
    from src.a2a_agents.trading.trading_agent_a2a_v2 import TradingA2AAgent

    return LangGraphAgentExecutorV2(TradingA2AAgent, config, **agent_kwargs)
