"""
SupervisorAgent A2A 서버 - V2

A2A 인터페이스를 사용하여 SupervisorAgent를 A2A 프로토콜로 제공합니다.
스트리밍과 풀링을 통합하여 처리하고, 표준화된 출력 형식을 사용합니다.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, cast

import pytz
import structlog
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskManager, TaskUpdater
from a2a.types import (
    DataPart,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from a2a.utils import new_agent_parts_message, new_agent_text_message

from src.a2a_integration.a2a_lg_client_utils_v2 import A2AClientManagerV2
from src.a2a_integration.a2a_lg_utils import (
    build_a2a_starlette_application,
    build_request_handler,
    create_agent_card,
    create_agent_skill,
)
from src.a2a_integration.cors_utils import create_cors_enabled_app
from src.lg_agents.util import load_env_file

load_env_file()

logger = structlog.get_logger(__name__)


class CustomSupervisorAgentA2A(AgentExecutor):
    """
    SupervisorAgent A2A 서버 - 표준 A2A 인터페이스 사용

    새로운 표준화된 A2A 인터페이스를 사용하여 스트리밍과 풀링을 통합하여 처리합니다.
    워크플로우 상태를 추적하고 상태 조회 기능을 제공합니다.
    """

    def __init__(self):
        self.agent_urls = {}
        self.task_store = InMemoryTaskStore()
        self.task_managers: Dict[str, TaskManager] = {}

    async def _ensure_agent_initialized(self):
        """Ensure dependent A2A agent endpoints are configured.

        - Resolves agent base URLs from environment variables.
        - Supports both local and Docker environments transparently.

        Env vars:
            - DATA_COLLECTOR_URL, ANALYSIS_URL, TRADING_URL
            - IS_DOCKER=true|false (auto-select hostnames)
        """
        if not self.agent_urls:
            try:
                # 환경에 따라 다른 A2A 에이전트들의 URL 설정
                is_docker = os.getenv("IS_DOCKER", "false").lower() == "true"

                if is_docker:
                    # Docker 환경에서는 컨테이너명 사용
                    self.agent_urls = {
                        "data_collector": os.getenv(
                            "DATA_COLLECTOR_URL", "http://data-collector-agent:8001"
                        ),
                        "analysis": os.getenv(
                            "ANALYSIS_URL", "http://analysis-agent:8002"
                        ),
                        "trading": os.getenv(
                            "TRADING_URL", "http://trading-agent:8003"
                        ),
                    }
                else:
                    # 로컬 환경에서는 localhost 사용
                    self.agent_urls = {
                        "data_collector": os.getenv(
                            "DATA_COLLECTOR_URL", "http://localhost:8001"
                        ),
                        "analysis": os.getenv("ANALYSIS_URL", "http://localhost:8002"),
                        "trading": os.getenv("TRADING_URL", "http://localhost:8003"),
                    }

                logger.info(
                    f" SupervisorA2AAgent initialized with URLs: {self.agent_urls}"
                )
            except Exception as e:
                logger.error(f"Failed to initialize SupervisorA2AAgent: {e}")
                raise RuntimeError(f"Agent initialization failed: {e}") from e

    async def _execute_workflow(
        self,
        input_dict: dict[str, Any],
        updater: TaskUpdater,
        context_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """Execute the end-to-end orchestration workflow.

        Steps:
            1) Determine workflow pattern by parsing user query
            2) Initialize and update A2A Task lifecycle (submitted → working)
            3) Execute pattern (data → analysis → trading)
            4) Finalize task state and build standardized output

        Args:
            input_dict: 표준 메시지 입력(dict)
            updater: A2A TaskUpdater (상태/메시지 전송)
            context_id: 요청 컨텍스트 식별자
            task_id: A2A Task ID

        Returns:
            dict: A2AOutput 준하는 응답 딕셔너리
        """
        try:
            # 1. 요청 분석 및 워크플로우 패턴 결정
            user_query = self._extract_user_query(input_dict)
            workflow_pattern = self._determine_workflow_pattern(user_query)

            logger.info(f" Workflow pattern determined: {workflow_pattern}")

            # TaskManager 초기화
            initial_message = new_agent_text_message(user_query)
            task_manager = TaskManager(
                task_id=task_id,
                context_id=context_id,
                task_store=self.task_store,
                initial_message=initial_message,
            )
            self.task_managers[task_id] = task_manager

            # Task 객체 초기화 및 metadata 설정
            task = await task_manager.get_task()
            if not task:
                # 새 Task 생성 - A2A 표준에 따라 submitted 상태로 시작
                task = Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        state=TaskState.submitted,
                        timestamp=datetime.now().isoformat(),
                    ),
                    history=[initial_message],
                    metadata={
                        "pattern": workflow_pattern,
                        "current_step": "submitted",
                        "completed_steps": [],
                        "pending_steps": self._get_workflow_steps(workflow_pattern),
                        "agent_responses": {},
                        "workflow_phase": "initialization",
                    },
                )
                await self.task_store.save(task)

                # 상태를 working으로 전환하여 작업 시작
                task.status = TaskStatus(
                    state=TaskState.working, timestamp=datetime.now().isoformat()
                )
                task.metadata["current_step"] = "initializing"
                task.metadata["workflow_phase"] = "execution"
                await self.task_store.save(task)

            # 2. 워크플로우 패턴에 따른 에이전트 실행
            workflow_result = await self._execute_workflow_pattern(
                workflow_pattern, user_query, context_id, updater, task_manager
            )

            # 3. Task 상태 최종 업데이트는 _send_a2a_output에서 처리
            # task = await task_manager.get_task()
            # if task and task.metadata:
            #     task.metadata["current_step"] = "completed"
            #     task.metadata["progress"] = 100
            #     task.metadata["workflow_phase"] = "completed"
            #     task.status = TaskStatus(
            #         state=TaskState.completed, timestamp=datetime.now().isoformat()
            #     )
            #     # 최종 완료 메시지 추가
            #     completion_message = (
            #         f" {workflow_pattern} 워크플로우가 성공적으로 완료되었습니다."
            #     )
            #     completion_message_obj = new_agent_text_message(
            #         str(completion_message), context_id=context_id, task_id=task_id
            #     )
            #     task.history.append(completion_message_obj)
            #     await self.task_store.save(task)

            # 4. 최종 결과 포맷팅
            return {
                "status": "completed",
                "text_content": workflow_result.get(
                    "summary", "워크플로우가 완료되었습니다."
                ),
                "data_content": {
                    "task_id": task_id,
                    "workflow_pattern": workflow_pattern,
                    "workflow_result": workflow_result,
                    "agent_type": "SupervisorAgent",
                    "completed_steps": task.metadata.get("completed_steps", [])
                    if task and task.metadata
                    else [],
                },
                "metadata": {"context_id": context_id},
                "final": True,
            }

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            # 에러 상태 업데이트
            task = await task_manager.get_task()
            if task:
                task.status = TaskStatus(
                    state=TaskState.failed, timestamp=datetime.now().isoformat()
                )
                await self.task_store.save(task)

            return {
                "status": "failed",
                "text_content": f"워크플로우 실행 중 오류가 발생했습니다: {str(e)}",
                "data_content": {"error": str(e), "agent_type": "SupervisorAgent"},
                "metadata": {"context_id": context_id},
                "final": True,
            }

    def _get_workflow_steps(self, pattern: str) -> list:
        """Return ordered steps for a workflow pattern.

        Patterns:
            - DATA_ONLY: [data_collection]
            - DATA_ANALYSIS: [data_collection, analysis]
            - FULL_WORKFLOW: [data_collection, analysis, trading]
        """
        if pattern == "DATA_ONLY":
            return ["data_collection"]
        elif pattern == "DATA_ANALYSIS":
            return ["data_collection", "analysis"]
        elif pattern == "FULL_WORKFLOW":
            return ["data_collection", "analysis", "trading"]
        return []

    def _extract_user_query(self, input_dict: dict[str, Any]) -> str:
        """Extract the user query text from A2A-standard input dict.

        Expected shape:
            {"messages": [{"role": "user", "content": "..."}, ...]}
        Falls back to ``str(input_dict)`` when not present.
        """
        if isinstance(input_dict, dict) and "messages" in input_dict:
            messages = input_dict["messages"]
            if messages and isinstance(messages, list):
                last_message = messages[-1]
                if isinstance(last_message, dict) and "content" in last_message:
                    return last_message["content"]
        return str(input_dict)

    def _is_status_query(self, query: str) -> tuple[bool, Optional[str]]:
        """Detect status-query intent and extract target Task ID if possible.

        Supports simple Korean/English trigger phrases and optional
        embedded ``task-<uuid>`` pattern.
        """
        query_lower = query.lower()

        # 상태 조회 패턴들
        status_patterns = [
            "상태조회:",
            "status:",
            "진행 상황",
            "현재 상태",
            "task status",
            "workflow status",
            "진행상황",
        ]

        for pattern in status_patterns:
            if pattern in query_lower:
                # Task ID 추출 시도
                import re

                task_id_match = re.search(r"task[_-]?([a-f0-9-]+)", query_lower)
                if task_id_match:
                    return True, task_id_match.group(1)
                # 최근 Task ID 사용
                if self.task_managers:
                    # 가장 최근 Task 반환 (가장 최근에 생성된 TaskManager의 Task ID)
                    recent_task_id = max(self.task_managers.keys())
                    return True, recent_task_id
                return True, None

        return False, None

    async def _get_workflow_status(self, task_id: Optional[str]) -> dict[str, Any]:
        """Return workflow status snapshot for a given Task ID.

        Includes progress, current/pending/completed steps, recent messages,
        and normalized A2A task status fields.
        """
        if not task_id or task_id not in self.task_managers:
            return {
                "status": "not_found",
                "text_content": "️ 현재 진행 중인 워크플로우가 없습니다. 새로운 요청을 시작해주세요.",
                "data_content": {
                    "error": "No active workflow found",
                    "available_tasks": list(self.task_managers.keys()),
                    "help": "주식 분석, 투자 전략, 포트폴리오 관리 등의 작업을 요청할 수 있습니다.",
                },
                "final": True,
            }

        # TaskManager로부터 Task 가져오기
        task_manager = self.task_managers[task_id]
        task = await task_manager.get_task()

        if not task:
            return {
                "status": "not_found",
                "text_content": "️ Task를 찾을 수 없습니다.",
                "data_content": {"error": "Task not found"},
                "final": True,
            }

        # A2A 표준 Task 상태 및 metadata 추출
        task_status = (
            task.status.state.name
            if task.status and task.status.state
            else "TASK_STATE_UNSPECIFIED"
        )
        task_timestamp = (
            task.status.timestamp.isoformat()
            if task.status and task.status.timestamp
            else task.created_at.isoformat()
        )
        current_step = (
            task.metadata.get("current_step", "initializing")
            if task.metadata
            else "initializing"
        )
        completed_steps = (
            task.metadata.get("completed_steps", []) if task.metadata else []
        )
        pending_steps = task.metadata.get("pending_steps", []) if task.metadata else []
        workflow_phase = (
            task.metadata.get("workflow_phase", "unknown")
            if task.metadata
            else "unknown"
        )

        # 진행률 계산
        if task.metadata and "progress" in task.metadata:
            progress = task.metadata["progress"]
        else:
            total_steps = len(completed_steps) + len(pending_steps)
            progress = (
                int((len(completed_steps) / total_steps) * 100)
                if total_steps > 0
                else 0
            )

        # 단계별 상태 메시지 매핑
        step_messages = {
            "data_collection": " 데이터 수집 에이전트가 시장 정보를 수집하고 있습니다",
            "analysis": " 분석 에이전트가 투자 분석을 수행하고 있습니다",
            "trading": " 거래 에이전트가 투자 전략을 수립하고 있습니다",
            "initializing": " 워크플로우를 초기화하고 있습니다",
        }

        current_message = step_messages.get(
            current_step, f"워크플로우 진행 중: {current_step}"
        )

        return {
            "status": task_status.lower().replace(
                "task_state_", ""
            ),  # A2A 표준에서 client-friendly 형식으로 변환
            "text_content": f"{current_message} ({progress}% 완료)",
            "data_content": {
                # A2A 표준 Task 정보
                "task_id": task.id,
                "context_id": task.context_id,
                "pattern": task.metadata.get("pattern", "FULL_WORKFLOW")
                if task.metadata
                else "FULL_WORKFLOW",
                # 워크플로우 상태 정보
                "current_step": current_step,
                "completed_steps": completed_steps,
                "pending_steps": pending_steps,
                "progress": progress,
                "workflow_phase": workflow_phase,
                # A2A Task 라이프사이클 정보
                "task_state": task_status,  # 원본 A2A TaskState
                "status": task_status.lower().replace(
                    "task_state_", ""
                ),  # Client-friendly 형식
                "created_at": task.created_at.isoformat(),
                "status_timestamp": task_timestamp,
                # 리치 메시지 및 응답 데이터
                "recent_messages": [
                    msg.parts[0].text
                    for msg in task.history[-5:]
                    if msg.parts and hasattr(msg.parts[0], "text")
                ],
                "agent_responses": task.metadata.get("agent_responses", {})
                if task.metadata
                else {},
                # 에러 정보 (있을 경우)
                "error": task.metadata.get("error")
                if task.metadata and "error" in task.metadata
                else None,
                "error_type": task.metadata.get("error_type")
                if task.metadata and "error_type" in task.metadata
                else None,
            },
            "metadata": {"context_id": task.context_id},
            "final": task_status
            in [
                "TASK_STATE_COMPLETED",
                "TASK_STATE_FAILED",
                "TASK_STATE_CANCELLED",
                "TASK_STATE_REJECTED",
            ],
        }

    def _determine_workflow_pattern(self, user_query: str) -> str:
        """Infer workflow pattern from the user query.

        Heuristics:
            - Trade-related keywords → FULL_WORKFLOW
            - Analysis-related keywords → DATA_ANALYSIS
            - Default → FULL_WORKFLOW (보수적으로 전체 실행)
        """
        query_lower = user_query.lower()
        # 거래 관련 키워드 체크
        trading_keywords = ["매수", "매도", "거래", "주문", "투자", "포트폴리오"]
        if any(keyword in query_lower for keyword in trading_keywords):
            return "FULL_WORKFLOW"  # 데이터 수집 -> 분석 -> 거래

        # 분석 관련 키워드 체크
        analysis_keywords = ["분석", "전망", "추천", "평가", "판단"]
        if any(keyword in query_lower for keyword in analysis_keywords):
            return "DATA_ANALYSIS"  # 데이터 수집 -> 분석

        return "FULL_WORKFLOW"

    async def _execute_workflow_pattern(
        self,
        pattern: str,
        user_query: str,
        context_id: str,
        updater: TaskUpdater,
        task_manager: TaskManager,
    ) -> dict[str, Any]:
        """Execute sub-agents according to a workflow pattern.

        The function updates the A2A Task metadata and history between steps
        to reflect user-visible progress.
        """
        results = {"pattern": pattern, "steps": []}

        try:
            # Task 정보 가져오기
            task = await task_manager.get_task()
            if not task:
                raise ValueError("Task not found")

            # 1. 데이터 수집
            # Task metadata 업데이트
            task.metadata["current_step"] = "data_collection"

            # Task 업데이트 메시지
            message = new_agent_text_message(
                " [데이터 수집 에이전트] 시장 데이터, 뉴스, 투자자 동향 수집을 시작합니다."
            )
            task.history.append(message)
            await self.task_store.save(task)
            await updater.update_status(
                message=message, state=TaskState.working, final=False
            )

            data_result = await self._call_agent(
                "data_collector", user_query, context_id
            )

            logger.info("===========" * 10)
            logger.info(f" [data_collector] 에이전트 작업 완료 - 응답: {data_result}")
            logger.info("===========" * 10)

            # 완료 상태 업데이트
            task.metadata["completed_steps"].append("data_collection")
            if "data_collection" in task.metadata["pending_steps"]:
                task.metadata["pending_steps"].remove("data_collection")
            task.metadata["agent_responses"]["data_collector"] = data_result

            success_message = " [데이터 수집 에이전트] 실시간 시장 데이터 및 관련 정보 수집을 완료했습니다."

            parts = [
                TextPart(text=str(success_message)),
                DataPart(
                    data=data_result if isinstance(data_result, dict) else {},
                    metadata={"agent_type": "data_collector"},
                ),
            ]
            message = new_agent_parts_message(
                parts, context_id=context_id, task_id=task.id
            )
            task.history.append(message)
            await self.task_store.save(task)
            await updater.update_status(
                message=message, state=TaskState.working, final=False
            )

            results["data_collection"] = data_result
            results["steps"].append("data_collection")

            if pattern == "DATA_ONLY":
                results["summary"] = (
                    " 데이터 수집 에이전트가 시장 데이터 수집을 완료했습니다."
                )
                return results

            # 2. 분석 실행 (DATA_ANALYSIS, FULL_WORKFLOW)
            if pattern in ["DATA_ANALYSIS", "FULL_WORKFLOW"]:
                # Task metadata 업데이트
                task.metadata["current_step"] = "analysis"

                analysis_message = " [분석 에이전트] 기술적 분석, 펀더멘털 분석, 심리지표 분석을 시작합니다."
                parts = [TextPart(text=str(analysis_message))]
                message = new_agent_parts_message(
                    parts, context_id=context_id, task_id=task.id
                )
                task.history.append(message)
                await self.task_store.save(task)
                await updater.update_status(
                    message=message, state=TaskState.working, final=False
                )

                analysis_input = f"{user_query}\n\n수집된 데이터: {data_result}"
                analysis_result = await self._call_agent(
                    "analysis", analysis_input, context_id
                )

                logger.info("===========" * 10)
                logger.info(f" [analysis] 에이전트 작업 완료 - 응답: {analysis_result}")
                logger.info("===========" * 10)

                # 완료 상태 업데이트
                task.metadata["completed_steps"].append("analysis")
                if "analysis" in task.metadata["pending_steps"]:
                    task.metadata["pending_steps"].remove("analysis")
                task.metadata["agent_responses"]["analysis"] = analysis_result

                analysis_success_message = (
                    " [분석 에이전트] 종합적인 투자 분석 및 신호 생성을 완료했습니다."
                )

                # 빈 배열 처리
                if isinstance(analysis_result, list) and len(analysis_result) == 0:
                    analysis_result = {}
                    logger.warning(
                        "Empty array received from analysis, converting to empty dict"
                    )

                parts = [
                    TextPart(text=str(analysis_success_message)),
                    DataPart(
                        data=analysis_result
                        if isinstance(analysis_result, dict)
                        else {},
                        metadata={"agent_type": "analysis"},
                    ),
                ]
                message = new_agent_parts_message(
                    parts, context_id=context_id, task_id=task.id
                )
                task.history.append(message)
                await self.task_store.save(task)
                await updater.update_status(
                    message=message, state=TaskState.working, final=False
                )

                results["analysis"] = analysis_result
                results["steps"].append("analysis")

                if pattern == "DATA_ANALYSIS":
                    results["summary"] = (
                        " 데이터 수집 및 투자 분석이 완료되었습니다. 분석 결과를 확인해주세요."
                    )
                    return results

            # 3. 거래 실행
            if pattern == "FULL_WORKFLOW":
                # Task metadata 업데이트
                task.metadata["current_step"] = "trading"

                trading_message = (
                    " [거래 에이전트] 포트폴리오 최적화 및 주문 준비를 시작합니다."
                )
                parts = [TextPart(text=str(trading_message))]
                message = new_agent_parts_message(
                    parts, context_id=context_id, task_id=task.id
                )
                task.history.append(message)
                await self.task_store.save(task)
                await updater.update_status(
                    message=message, state=TaskState.working, final=False
                )

                trading_input = f"질문: {user_query}\n\n분석 결과: {analysis_result}"
                trading_result = await self._call_agent(
                    "trading", trading_input, context_id
                )

                logger.info("===========" * 10)
                logger.info(f" [trading] 에이전트 작업 완료 - 응답: {trading_result}")
                logger.info("===========" * 10)

                # 완료 상태 업데이트
                task.metadata["completed_steps"].append("trading")
                if "trading" in task.metadata["pending_steps"]:
                    task.metadata["pending_steps"].remove("trading")
                task.metadata["agent_responses"]["trading"] = trading_result

                trading_success_message = (
                    " [거래 에이전트] 거래 전략 수립 및 리스크 검토를 완료했습니다."
                )

                # 빈 배열 처리
                if isinstance(trading_result, list) and len(trading_result) == 0:
                    trading_result = {}
                    logger.warning(
                        "Empty array received from trading, converting to empty dict"
                    )

                parts = [
                    TextPart(text=str(trading_success_message)),
                    DataPart(
                        data=trading_result
                        if not isinstance(trading_result, dict)
                        else {},
                        metadata={"agent_type": "trading"},
                    ),
                ]
                message = new_agent_parts_message(
                    parts, context_id=context_id, task_id=task.id
                )
                task.history.append(message)
                await self.task_store.save(task)
                await updater.update_status(
                    message=message, state=TaskState.working, final=False
                )

                results["trading"] = trading_result
                results["steps"].append("trading")
                results["summary"] = (
                    " 모든 워크플로우가 성공적으로 완료되었습니다. 거래 전략이 수립되었습니다."
                )

            return results

        except Exception as e:
            logger.error(f"Workflow pattern execution failed: {e}")

            # Task 에러 상태 업데이트 - A2A 표준 error handling
            try:
                task = await task_manager.get_task()
                if task:
                    task.status = TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            f" 워크플로우 실행 중 오류 발생: {str(e)}"
                        ),
                        timestamp=datetime.now(
                            tz=pytz.timezone("Asia/Seoul")
                        ).isoformat(),
                    )
                    task.metadata["error"] = str(e)
                    task.metadata["error_type"] = type(e).__name__
                    task.metadata["workflow_phase"] = "failed"

                    error_message = f" 워크플로우 실행 중 오류 발생: {str(e)}"
                    parts = [TextPart(text=str(error_message))]
                    task.history.append(
                        new_agent_parts_message(
                            parts, context_id=context_id, task_id=task.id
                        )
                    )
                    await self.task_store.save(task)

                    logger.error(
                        f"Task {task.id} failed with error: {e}", exc_info=True
                    )
            except Exception as task_error:
                logger.error(
                    f"Failed to update task with error state: {task_error}",
                    exc_info=True,
                )

            results["error"] = str(e)
            results["summary"] = f" 워크플로우 실행 중 오류가 발생했습니다: {str(e)}"
            return results

    async def _call_agent(
        self, agent_type: str, query: str, context_id: str
    ) -> dict[str, Any]:
        """Call a downstream A2A agent via the A2A Client SDK (V2).

        Args:
            agent_type: "data_collector" | "analysis" | "trading"
            query: 사용자 프롬프트 또는 직렬화된 입력
            context_id: 상관관계 식별자 (thread/task)

        Returns:
            dict: 에이전트 표준 응답 (데이터 파트 포함 가능)
        """
        agent_url = self.agent_urls.get(agent_type)
        if not agent_url:
            raise ValueError(f"Unknown agent type: {agent_type}")

        # A2A 호출 메시지 구성
        input_data = {"messages": [{"role": "user", "content": query}]}

        try:
            # A2A SDK를 사용
            a2a_client_manager = A2AClientManagerV2(
                base_url=agent_url, streaming=False, retry_delay=5.0
            )
            a2a_client = await a2a_client_manager.initialize()
            result = await a2a_client.send_data(input_data)
            return result

        except Exception as e:
            error_msg = f"Failed to call {agent_type} agent via A2A SDK: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute supervisor workflow using the standardized A2A interface.

        - Parses input, handles status queries, or runs a full workflow.
        - Streams intermediate updates via TaskUpdater when appropriate.
        - Emits a final message/artifact on completion.
        """
        try:
            logger.info(" [SUPERVISOR] 워크플로우 오케스트레이션 시작 - A2A Protocol")

            # Initialize agent if needed
            await self._ensure_agent_initialized()

            # Process input from A2A context
            input_dict = await self._process_input(context)
            user_query = self._extract_user_query(input_dict)

            # Setup task updater
            task_id = cast(str, context.task_id)
            context_id = str(getattr(context, "context_id", task_id))

            updater = TaskUpdater(
                event_queue=event_queue, task_id=task_id, context_id=context_id
            )

            # 상태 조회 요청인지 확인
            is_status, status_task_id = self._is_status_query(user_query)

            if is_status:
                result = await self._get_workflow_status(status_task_id)
            else:
                await updater.start_work()
                result = await self._execute_workflow(
                    input_dict, updater, str(context_id), task_id
                )

            logger.info(
                f"[SUPERVISOR] 작업 처리 완료 - 상태: {result.get('status', 'unknown')}"
            )

            # Send result based on A2AOutput format
            await self._send_a2a_output(result, updater, event_queue)

        except Exception as e:
            logger.error(f"SupervisorAgent execution failed: {e}")
            # Send error status
            try:
                await updater.update_status(
                    TaskState.failed,
                    new_agent_text_message(
                        f"작업 중 오류가 발생했습니다: {str(e)}",
                        context_id=context_id,
                        task_id=task_id,
                    ),
                    final=True,
                )
            except Exception as update_error:
                logger.error(f"Failed to update error status: {update_error}")
            raise

    async def _process_input(self, context: RequestContext) -> dict:
        """Parse input from the A2A request context into standard dict."""
        query = context.get_user_input()

        # Try to parse structured data
        try:
            data = json.loads(query)
            if isinstance(data, dict) and "messages" in data:
                return data
        except json.JSONDecodeError:
            pass

        # Fallback to simple message format
        return {"messages": [{"role": "user", "content": query}]}

    async def _send_a2a_output(
        self, output: dict, updater: TaskUpdater, event_queue: EventQueue
    ) -> None:
        """Send an A2AOutput-like dict as Text/Data parts to the client.

        - Maps ``text_content`` → TextPart, ``data_content`` → DataPart
        - Uses updater.complete for final messages, otherwise enqueues events
        """
        try:
            status = output.get("status", "working")
            text_content = output.get("text_content")
            data_content = output.get("data_content")
            is_final = output.get("final", False)

            # Build message parts
            parts = []

            # Add text part if present
            if text_content:
                parts.append(TextPart(text=str(text_content)))

            # Add data part if present
            if data_content:
                # 구조화된 응답 데이터 보장
                structured_data = {
                    "data_content": data_content,
                    "metadata": {
                        "timestamp": datetime.now().isoformat(),
                        "agent": "supervisor",
                        "status": status,
                    },
                }
                parts.append(DataPart(data=dict(structured_data)))

            # Send message if we have parts
            if parts:
                message = new_agent_parts_message(parts)

                # final인 경우 complete, 아니면 일반 메시지
                if is_final:
                    # Task 상태를 completed로 명시적으로 업데이트
                    await updater.update_status(
                        message=message, state=TaskState.completed, final=True
                    )
                    await event_queue.enqueue_event(message)
                    logger.info(f"Task completed with state=TaskState.completed")
                else:
                    # 중간 상태 업데이트
                    await event_queue.enqueue_event(message)

                logger.info(f"Sent message with {len(parts)} parts (final={is_final})")

        except Exception as e:
            logger.error(f"Failed to send A2A output: {e}")
            raise

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Cancel an ongoing task and emit a TaskStatusUpdateEvent (final)."""
        logger.info(f"Cancelling task: {context.task_id}")

        if context.current_task:
            updater = TaskUpdater(
                event_queue=event_queue,
                task_id=context.current_task.id,
                context_id=str(context.context_id),
            )
            await updater.cancel()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.current_task.id,
                    context_id=str(context.context_id),
                    status=TaskState.cancelled,
                    final=True,
                )
            )
            logger.info(f"Task {context.context_id} cancelled")

    def get_agent_card(self, url: str):
        """A2A AgentCard 생성.

        Docker 환경에서는 자동으로 컨테이너 기반 호스트명을 사용하여 URL을 보정한다.
        """
        if os.getenv("IS_DOCKER", "false").lower() == "true":
            url = f"http://supervisor-agent:{os.getenv('AGENT_PORT', '8000')}"

        _skill = create_agent_skill(
            skill_id="stock_investment_orchestrator",
            name="AI 주식 투자 워크플로우 오케스트레이션",
            description="사용자 요청을 분석하여 데이터 수집, 분석, 거래 실행의 최적 워크플로우를 결정하고 실행합니다",
            tags=["supervisor", "orchestration", "workflow", "stock-investment"],
            examples=[
                "삼성전자 주식을 분석하고 매수 전략을 제시해주세요",
                "KOSPI 상위 10개 종목을 분석하여 포트폴리오를 구성해주세요",
            ],
        )

        return create_agent_card(
            name="SupervisorAgent",
            description="FastCampus - MCP & A2A - AI 주식 투자 시스템의 오케스트레이터",
            url=url,
            version="1.0.0",
            skills=[_skill],
        )


def main():
    """SupervisorAgent A2A 서버 실행"""
    # 로깅 설정
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # SupervisorAgent는 초기화 시 자동으로 URL 설정됨
    supervisor_executor = CustomSupervisorAgentA2A()

    try:
        is_docker = os.getenv("IS_DOCKER", "false").lower() == "true"
        host = os.getenv("AGENT_HOST", "localhost" if not is_docker else "0.0.0.0")
        port = int(os.getenv("AGENT_PORT", "8000"))
        url = f"http://{host}:{port}"

        # A2A 핸들러와 애플리케이션 생성
        handler = build_request_handler(supervisor_executor)
        server_app = build_a2a_starlette_application(
            agent_card=supervisor_executor.get_agent_card(url),
            handler=handler,
        )

        # CORS가 적용된 앱 생성
        app = create_cors_enabled_app(server_app)

        logger.info(f" SupervisorAgent A2A server starting at {url} with CORS enabled")
        # uvicorn 서버 설정 - 타임아웃 증가 및 스트리밍 최적화
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            reload=False,
            timeout_keep_alive=1000,
            timeout_notify=1000,
            ws_ping_interval=30,
            ws_ping_timeout=60,
            limit_max_requests=None,
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        server.run()

    except Exception as e:
        logger.error(f"서버 시작 중 오류 발생: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
