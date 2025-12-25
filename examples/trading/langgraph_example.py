#!/usr/bin/env python3
"""
Trading Agent - LangGraph 레벨 직접 호출 예제

ReactTradingAgent를 직접 import하여 사용하는 예제입니다.
리스크 관리와 Human-in-the-Loop 승인을 통한 안전한 거래 실행을 수행합니다.

실행 전제 조건:
- MCP 서버들이 실행 중이어야 함 (./1-run-all-services.sh)
- 특히 포트 8030(trading), 8034(portfolio)가 활성화되어 있어야 함
"""

import asyncio
import json
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 공통 모듈 import
from examples.common.logging import (  # noqa: E402
    get_result_filename,
)
from examples.common.server_checks import check_mcp_servers  # noqa: E402
from src.lg_agents.trading_agent import (  # noqa: E402
    create_trading_agent,
    execute_trading,
)


def print_section(title: str):
    """섹션 구분선 출력"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


async def main():
    """메인 실행 함수"""

    print_section("Trading Agent - LangGraph 예제")
    print("create_react_agent를 사용한 안전한 거래 실행을 수행합니다.")

    # 1. MCP 서버 상태 확인
    if not await check_mcp_servers("trading"):
        print("\n일부 MCP 서버가 실행되지 않았습니다.")
        print("해결 방법: ./1-run-all-services.sh 실행")

    # 2. Trading Agent 초기화
    print_section("TradingAgent 초기화")

    print("create_react_agent 기반 TradingAgent 생성 중...")
    agent = await create_trading_agent(is_debug=True)

    if not agent:
        print("TradingAgent 생성 실패")
        return

    print_section("거래 실행")

    test_request = {
        "symbols": ["005930"],  # 삼성전자
        "trading_signal": "BUY",
        "analysis_result": {
            "investment_signal": "BUY",
            "integrated_score": 0.75,
            "confidence_level": 0.85,
            "dimension_analysis": {
                "technical": {"score": 0.8, "insights": "기술적 지표 강세"},
                "fundamental": {"score": 0.7, "insights": "밸류에이션 매력적"},
            },
        },
        "user_question": """
삼성전자(005930)를 매수하려고 합니다.
다음 도구들을 모두 사용해서 안전한 거래를 실행해주세요:

1. 포트폴리오 리스크 관리:
    - get_portfolio_status로 현재 포트폴리오 상태 확인
    - calculate_position_size로 적정 포지션 규모 계산
    - assess_portfolio_risk로 포트폴리오 리스크 평가
    - calculate_var로 Value at Risk 계산

2. 주문 준비 및 검증:
    - get_account_balance로 계좌 잔고 확인
    - check_trading_limits로 거래 한도 검증
    - validate_order_parameters로 주문 파라미터 검증
    - simulate_order_impact로 주문 체결 영향 시뮬레이션

3. 거래 실행:
    - place_order로 실제 주문 실행 (또는 모의 주문)
    - get_order_status로 주문 상태 확인
    - update_portfolio로 포트폴리오 업데이트

4. 사후 관리:
    - set_stop_loss로 손절매 설정
    - set_take_profit로 익절매 설정
    - log_trade_activity로 거래 활동 기록

모든 도구를 빠짐없이 호출하여 리스크를 최소화하고 안전한 거래를 실행해주세요.
Human-in-the-Loop 승인이 필요한 경우 명확한 리스크 분석을 제공해주세요.""",
    }

    print(f"거래 종목: {test_request['symbols']}")
    print(f"거래 신호: {test_request['trading_signal']}")
    print(f"통합 점수: {test_request['analysis_result']['integrated_score']}")
    print(f"질문: {test_request['user_question'][:100]}...")

    try:
        print("\n거래 실행 중... (최대 120초 소요)")
        print("여러 도구를 호출하므로 시간이 걸릴 수 있습니다...")

        result = await asyncio.wait_for(
            execute_trading(
                agent=agent,
                analysis_result=test_request["analysis_result"],
                user_question=test_request["user_question"],
            ),
            timeout=120.0,
        )
        print("\n모든 거래 도구 호출 완료")
    except asyncio.TimeoutError:
        print("\n거래 실행 타임아웃 (120초)")
        result = {
            "success": False,
            "error": "Trading execution timeout after 120 seconds",
            "messages": [],
        }
    except Exception as e:
        print(f"\n실행 중 오류 발생: {str(e)}")
        import traceback

        traceback.print_exc()
        result = {
            "success": False,
            "error": str(e),
            "messages": [],
        }

    # 5. 결과 출력
    print_section("거래 결과")

    if isinstance(result, dict) and result.get("success"):
        print("거래 프로세스 완료!")

        trading_result = result.get("result", {})

        # 도구 호출 검증 로직 추가
        tool_calls = trading_result.get("tool_calls_made", 0)
        print("\n도구 호출 검증:")
        print(f"  - 도구 호출 횟수: {tool_calls}회")
    elif isinstance(result, dict):
        print(f"거래 실패: {result.get('error', 'Unknown error')}")

    # 6. 전체 결과를 JSON 파일로 저장
    output_dir = Path("../../logs/examples/langgraph")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / get_result_filename("trading_result")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n전체 결과가 {output_file}에 저장되었습니다.")

    print_section("테스트 완료")


if __name__ == "__main__":
    asyncio.run(main())
