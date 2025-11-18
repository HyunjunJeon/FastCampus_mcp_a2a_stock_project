# FastCampus MCP A2A 시스템 - 실전 사용 가이드

> **작성일**: 2025-11-18
> **목적**: 개발자를 위한 실전 코드 예제 및 사용법 가이드

---

## 📋 목차

1. [빠른 시작](#빠른-시작)
2. [에이전트별 사용 예제](#에이전트별-사용-예제)
3. [MCP 도구 사용법](#mcp-도구-사용법)
4. [워크플로우 커스터마이징](#워크플로우-커스터마이징)
5. [에러 처리 및 디버깅](#에러-처리-및-디버깅)
6. [성능 최적화 팁](#성능-최적화-팁)
7. [프로덕션 배포](#프로덕션-배포)

---

## 빠른 시작

### 1. 환경 설정

```bash
# 1. 프로젝트 클론
git clone <repository-url>
cd FastCampus_mcp_a2a_stock_project

# 2. Python 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 의존성 설치
pip install -e .

# 4. 환경 변수 설정
cp .env.example .env
```

### 2. .env 파일 설정

```env
# OpenAI API (필수)
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxx

# 키움증권 API (선택 - 실제 거래 시 필요)
KIWOOM_API_KEY=your_api_key
KIWOOM_SECRET_KEY=your_secret_key
KIWOOM_ACCOUNT_NO=your_account_number

# TAVILY API (필수 - 웹 검색용)
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxxxxxxx

# Naver Search API (선택 - 뉴스 수집용)
NAVER_CLIENT_ID=your_client_id
NAVER_CLIENT_SECRET=your_client_secret

# FRED API (선택 - 거시경제 데이터용)
FRED_API_KEY=your_fred_api_key

# DART API (선택 - 전자공시용)
DART_API_KEY=your_dart_api_key
```

### 3. 첫 번째 테스트

```python
# test_basic.py
import asyncio
from src.lg_agents.data_collector_agent import (
    create_data_collector_agent,
    collect_data
)

async def main():
    # DataCollector 에이전트 생성
    agent = await create_data_collector_agent(is_debug=True)

    # 데이터 수집 실행
    result = await collect_data(
        agent=agent,
        symbols=["005930"],  # 삼성전자
        user_question="삼성전자의 최신 정보를 수집해주세요"
    )

    # 결과 출력
    print("✅ 수집 완료!")
    print(f"도구 호출 횟수: {result['result']['tool_calls_made']}")
    print(f"\n응답:\n{result['result']['raw_response']}")

if __name__ == "__main__":
    asyncio.run(main())
```

```bash
# 실행
python test_basic.py
```

---

## 에이전트별 사용 예제

### 1. DataCollectorAgent - 데이터 수집

#### 기본 사용법

```python
import asyncio
from src.lg_agents.data_collector_agent import (
    create_data_collector_agent,
    collect_data
)

async def example_data_collection():
    """기본 데이터 수집 예제"""

    # 에이전트 생성
    agent = await create_data_collector_agent(is_debug=True)

    # 삼성전자 데이터 수집
    result = await collect_data(
        agent=agent,
        symbols=["005930"],
        data_types=["price", "news", "investor"],
        user_question="삼성전자의 최신 정보를 수집해주세요"
    )

    # 결과 확인
    if result["success"]:
        print("✅ 데이터 수집 성공!")
        print(f"수집된 종목: {result['result']['symbols_collected']}")
        print(f"도구 호출: {result['result']['tool_calls_made']}회")
        print(f"\n수집 내용:\n{result['result']['raw_response']}")
    else:
        print(f"❌ 실패: {result['error']}")

asyncio.run(example_data_collection())
```

#### 다중 종목 수집

```python
async def example_multiple_stocks():
    """여러 종목 동시 수집"""

    agent = await create_data_collector_agent()

    # 삼성전자, SK하이닉스, NAVER 동시 수집
    result = await collect_data(
        agent=agent,
        symbols=["005930", "000660", "035420"],
        user_question="반도체 및 IT 대장주 3개의 정보를 수집해주세요"
    )

    print(result['result']['raw_response'])

asyncio.run(example_multiple_stocks())
```

#### 특정 데이터 타입만 수집

```python
async def example_specific_data_types():
    """특정 타입 데이터만 수집"""

    agent = await create_data_collector_agent()

    # 뉴스만 수집
    result = await collect_data(
        agent=agent,
        symbols=["005930"],
        data_types=["news"],
        user_question="삼성전자 관련 최신 뉴스를 수집해주세요"
    )

    print(result['result']['raw_response'])

asyncio.run(example_specific_data_types())
```

---

### 2. AnalysisAgent - 투자 분석

#### 기본 분석

```python
import asyncio
from src.lg_agents.analysis_agent import (
    create_analysis_agent,
    analyze
)

async def example_basic_analysis():
    """기본 투자 분석 예제"""

    # 에이전트 생성
    agent = await create_analysis_agent(is_debug=True)

    # 삼성전자 분석
    result = await analyze(
        agent=agent,
        symbols=["005930"],
        user_question="삼성전자에 대한 종합 투자 분석을 해주세요"
    )

    # 결과 확인
    if result["success"]:
        print("✅ 분석 완료!")
        print(f"분석 종목: {result['result']['symbols_analyzed']}")
        print(f"사용된 도구: {result['result']['tool_calls_made']}개")
        print(f"\n분석 결과:\n{result['result']['raw_analysis']}")
    else:
        print(f"❌ 실패: {result['error']}")

asyncio.run(example_basic_analysis())
```

#### 수집된 데이터와 함께 분석

```python
async def example_analysis_with_collected_data():
    """DataCollector 결과를 AnalysisAgent에 전달"""

    from src.lg_agents.data_collector_agent import (
        create_data_collector_agent,
        collect_data
    )
    from src.lg_agents.analysis_agent import (
        create_analysis_agent,
        analyze
    )

    # 1. 데이터 수집
    collector = await create_data_collector_agent()
    collected = await collect_data(
        agent=collector,
        symbols=["005930"],
        user_question="삼성전자 데이터 수집"
    )

    # 2. 수집된 데이터로 분석
    analyzer = await create_analysis_agent()
    analysis = await analyze(
        agent=analyzer,
        symbols=["005930"],
        collected_data=collected,
        user_question="수집된 데이터를 바탕으로 투자 분석해주세요"
    )

    print(analysis['result']['raw_analysis'])

asyncio.run(example_analysis_with_collected_data())
```

#### 특정 분석 차원만 활용

```python
async def example_specific_analysis_dimension():
    """특정 분석 차원만 요청"""

    agent = await create_analysis_agent()

    # 기술적 분석만 수행
    result = await analyze(
        agent=agent,
        symbols=["005930"],
        user_question="""삼성전자의 기술적 분석만 수행해주세요.
        - RSI, MACD, 볼린저밴드
        - 추세 분석
        - 지지/저항선"""
    )

    print(result['result']['raw_analysis'])

asyncio.run(example_specific_analysis_dimension())
```

---

### 3. TradingAgent - 거래 실행

#### 기본 거래 실행

```python
import asyncio
from src.lg_agents.trading_agent import (
    create_trading_agent,
    execute_trading
)

async def example_basic_trading():
    """기본 거래 실행 예제"""

    # 에이전트 생성
    agent = await create_trading_agent(is_debug=True)

    # 분석 결과 (AnalysisAgent로부터)
    analysis_result = {
        "symbols": ["005930"],
        "trading_signal": "BUY",
        "confidence": 0.85,
        "target_price": 75000,
        "stop_loss": 70000
    }

    # 거래 실행
    result = await execute_trading(
        agent=agent,
        analysis_result=analysis_result,
        user_question="삼성전자를 매수해주세요"
    )

    # 결과 확인
    if result["success"]:
        print("✅ 거래 완료!")
        print(f"\n실행 내용:\n{result['result']['raw_response']}")
    else:
        print(f"❌ 실패: {result['error']}")

asyncio.run(example_basic_trading())
```

#### 전체 워크플로우 (수집 → 분석 → 거래)

```python
async def example_full_workflow():
    """전체 투자 워크플로우 예제"""

    from src.lg_agents.data_collector_agent import (
        create_data_collector_agent, collect_data
    )
    from src.lg_agents.analysis_agent import (
        create_analysis_agent, analyze
    )
    from src.lg_agents.trading_agent import (
        create_trading_agent, execute_trading
    )

    symbol = "005930"  # 삼성전자

    print("📊 1단계: 데이터 수집")
    collector = await create_data_collector_agent()
    collected = await collect_data(
        agent=collector,
        symbols=[symbol],
        user_question=f"{symbol} 데이터 수집"
    )
    print(f"✅ 수집 완료: {collected['result']['tool_calls_made']}개 도구 사용\n")

    print("📈 2단계: 투자 분석")
    analyzer = await create_analysis_agent()
    analysis = await analyze(
        agent=analyzer,
        symbols=[symbol],
        collected_data=collected,
        user_question="종합 투자 분석"
    )
    print(f"✅ 분석 완료: {analysis['result']['tool_calls_made']}개 도구 사용\n")

    print("💰 3단계: 거래 실행")
    trader = await create_trading_agent()
    trading = await execute_trading(
        agent=trader,
        analysis_result=analysis["result"],
        user_question="분석 결과에 따라 거래 실행"
    )
    print(f"✅ 거래 완료!\n")

    # 최종 결과
    print("=" * 60)
    print("📋 최종 결과")
    print("=" * 60)
    print(trading['result']['raw_response'])

asyncio.run(example_full_workflow())
```

---

### 4. SupervisorAgent - 통합 오케스트레이션

#### 기본 사용법

```python
import asyncio
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from src.lg_agents.supervisor_agent import SupervisorAgent

async def example_supervisor_basic():
    """SupervisorAgent 기본 사용"""

    # SupervisorAgent 생성
    supervisor = SupervisorAgent(
        model=ChatOpenAI(model="gpt-4o-mini", temperature=0),
        is_debug=True
    )

    # 사용자 요청 실행
    result = await supervisor.graph.ainvoke(
        {"messages": [HumanMessage(content="삼성전자 주가를 분석해줘")]},
        config={"configurable": {"thread_id": "test-001"}}
    )

    # 결과 확인
    print(f"워크플로우 패턴: {result['workflow_pattern']}")
    print(f"최종 응답: {result['final_response']}")

asyncio.run(example_supervisor_basic())
```

#### 다양한 워크플로우 패턴

```python
async def example_workflow_patterns():
    """다양한 워크플로우 패턴 예제"""

    from src.lg_agents.supervisor_agent import SupervisorAgent
    from langchain_core.messages import HumanMessage

    supervisor = SupervisorAgent(is_debug=True)

    # 패턴 1: DATA_ONLY (데이터만 조회)
    print("📊 패턴 1: DATA_ONLY")
    result1 = await supervisor.graph.ainvoke(
        {"messages": [HumanMessage(content="삼성전자 현재가 알려줘")]},
        config={"configurable": {"thread_id": "pattern-1"}}
    )
    print(f"패턴: {result1['workflow_pattern']}\n")

    # 패턴 2: DATA_ANALYSIS (데이터 + 분석)
    print("📈 패턴 2: DATA_ANALYSIS")
    result2 = await supervisor.graph.ainvoke(
        {"messages": [HumanMessage(content="삼성전자 투자 분석해줘")]},
        config={"configurable": {"thread_id": "pattern-2"}}
    )
    print(f"패턴: {result2['workflow_pattern']}\n")

    # 패턴 3: FULL_WORKFLOW (데이터 + 분석 + 거래)
    print("💰 패턴 3: FULL_WORKFLOW")
    result3 = await supervisor.graph.ainvoke(
        {"messages": [HumanMessage(content="삼성전자 매수해줘")]},
        config={"configurable": {"thread_id": "pattern-3"}}
    )
    print(f"패턴: {result3['workflow_pattern']}\n")

asyncio.run(example_workflow_patterns())
```

---

## MCP 도구 사용법

### 1. MCP 도구 직접 호출

```python
import asyncio
from src.lg_agents.base.mcp_config import load_data_collector_tools

async def example_direct_mcp_call():
    """MCP 도구 직접 호출 예제"""

    # MCP 도구 로딩
    tools = await load_data_collector_tools()

    # 도구 목록 확인
    print("사용 가능한 도구:")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description}")

    # 특정 도구 찾기 (예: 실시간 시세 조회)
    realtime_price_tool = next(
        (t for t in tools if "realtime_price" in t.name.lower()),
        None
    )

    if realtime_price_tool:
        # 도구 직접 실행
        result = await realtime_price_tool.ainvoke({
            "stock_codes": ["005930"],
            "fields": ["current_price", "change_rate"]
        })
        print(f"\n도구 실행 결과:\n{result}")

asyncio.run(example_direct_mcp_call())
```

### 2. 커스텀 MCP 도구 추가

```python
# src/lg_agents/base/mcp_config.py에 추가

async def load_custom_tools():
    """커스텀 MCP 도구 로딩"""
    from langchain_mcp import MCPToolkit

    # 커스텀 MCP 서버 연결
    toolkit = MCPToolkit(
        server_params={
            "host": "localhost",
            "port": 9000,
            "server_name": "custom_mcp_server"
        }
    )

    tools = await toolkit.get_tools()
    return tools
```

---

## 워크플로우 커스터마이징

### 1. 커스텀 노드 추가

```python
from src.lg_agents.base.base_graph_agent import BaseGraphAgent
from langgraph.graph import StateGraph, START, END

class CustomAgent(BaseGraphAgent):
    """커스텀 에이전트"""

    def init_nodes(self, graph: StateGraph):
        """노드 초기화"""
        graph.add_node("step1", self._step1)
        graph.add_node("step2", self._step2)
        graph.add_node("step3", self._step3)

    def init_edges(self, graph: StateGraph):
        """엣지 초기화"""
        graph.add_edge(START, "step1")
        graph.add_edge("step1", "step2")
        graph.add_edge("step2", "step3")
        graph.add_edge("step3", END)

    async def _step1(self, state, config):
        """1단계 처리"""
        print("Step 1 실행")
        state["step1_result"] = "Step 1 완료"
        return state

    async def _step2(self, state, config):
        """2단계 처리"""
        print("Step 2 실행")
        state["step2_result"] = "Step 2 완료"
        return state

    async def _step3(self, state, config):
        """3단계 처리"""
        print("Step 3 실행")
        state["final_result"] = "모든 단계 완료"
        return state
```

### 2. 조건부 라우팅 추가

```python
def init_edges(self, graph: StateGraph):
    """조건부 엣지"""
    graph.add_edge(START, "analyze")

    # 조건부 라우팅
    graph.add_conditional_edges(
        "analyze",
        self._route_based_on_risk,  # 라우팅 함수
        {
            "high_risk": "human_approval",
            "low_risk": "auto_execute",
        }
    )

    graph.add_edge("human_approval", "execute")
    graph.add_edge("auto_execute", "execute")
    graph.add_edge("execute", END)

def _route_based_on_risk(self, state):
    """리스크 기반 라우팅"""
    risk_score = state.get("risk_score", 0)

    if risk_score > 0.7:
        return "high_risk"
    else:
        return "low_risk"
```

---

## 에러 처리 및 디버깅

### 1. 에러 처리 패턴

```python
async def safe_execution():
    """안전한 실행 패턴"""

    try:
        agent = await create_data_collector_agent(is_debug=True)
        result = await collect_data(
            agent=agent,
            symbols=["005930"],
            user_question="데이터 수집"
        )

        if result["success"]:
            print("✅ 성공!")
            return result
        else:
            print(f"❌ 실패: {result['error']}")
            return None

    except Exception as e:
        print(f"🔥 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return None
```

### 2. 디버깅 로그 활성화

```python
import logging
import structlog

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# structlog 설정
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
)

# 에이전트 실행 (디버그 모드)
agent = await create_data_collector_agent(is_debug=True)
```

### 3. 상태 추적

```python
async def debug_state_tracking():
    """상태 추적 디버깅"""

    from src.lg_agents.supervisor_agent import SupervisorAgent
    from langchain_core.messages import HumanMessage

    supervisor = SupervisorAgent(is_debug=True)

    # 스트리밍으로 각 단계 추적
    async for event in supervisor.graph.astream(
        {"messages": [HumanMessage(content="삼성전자 분석")]},
        config={"configurable": {"thread_id": "debug-001"}}
    ):
        print(f"\n📍 이벤트: {event}")
        for key, value in event.items():
            print(f"  {key}: {value}")
```

---

## 성능 최적화 팁

### 1. 병렬 실행

```python
import asyncio

async def parallel_analysis():
    """여러 종목 병렬 분석"""

    from src.lg_agents.analysis_agent import (
        create_analysis_agent, analyze
    )

    agent = await create_analysis_agent()

    # 여러 종목 동시 분석
    symbols_list = [
        ["005930"],  # 삼성전자
        ["000660"],  # SK하이닉스
        ["035420"],  # NAVER
    ]

    # 병렬 실행
    tasks = [
        analyze(agent, symbols, user_question=f"{symbols[0]} 분석")
        for symbols in symbols_list
    ]

    results = await asyncio.gather(*tasks)

    for i, result in enumerate(results):
        print(f"\n종목 {symbols_list[i]}: {result['success']}")
```

### 2. 캐싱 활용

```python
from functools import lru_cache
import pickle

# 에이전트 캐싱
@lru_cache(maxsize=10)
async def get_cached_agent(agent_type: str):
    """에이전트 캐시"""
    if agent_type == "data_collector":
        return await create_data_collector_agent()
    elif agent_type == "analysis":
        return await create_analysis_agent()
    elif agent_type == "trading":
        return await create_trading_agent()
```

### 3. 배치 처리

```python
async def batch_processing():
    """배치 데이터 수집"""

    from src.lg_agents.data_collector_agent import (
        create_data_collector_agent, collect_data
    )

    agent = await create_data_collector_agent()

    # 종목 배치
    batches = [
        ["005930", "000660", "035420"],  # Batch 1
        ["051910", "006400", "035720"],  # Batch 2
        ["207940", "068270", "028260"],  # Batch 3
    ]

    for i, batch in enumerate(batches):
        print(f"\n배치 {i+1} 처리 중...")
        result = await collect_data(
            agent=agent,
            symbols=batch,
            user_question=f"배치 {i+1} 데이터 수집"
        )
        print(f"✅ 배치 {i+1} 완료")
```

---

## 프로덕션 배포

### 1. Docker 배포

```bash
# docker-compose.full.yml 사용
docker-compose -f docker-compose.full.yml up -d

# 로그 확인
docker-compose -f docker-compose.full.yml logs -f

# 특정 서비스만 재시작
docker-compose -f docker-compose.full.yml restart market_domain
```

### 2. 환경별 설정

```python
# config.py
import os
from enum import Enum

class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

class Config:
    """환경별 설정"""

    def __init__(self):
        self.env = os.getenv("ENVIRONMENT", "development")

    @property
    def is_production(self):
        return self.env == Environment.PRODUCTION

    @property
    def llm_model(self):
        """환경별 LLM 모델"""
        if self.is_production:
            return "gpt-4o"
        else:
            return "gpt-4o-mini"

    @property
    def debug_mode(self):
        """디버그 모드"""
        return not self.is_production

# 사용
config = Config()
agent = await create_data_collector_agent(is_debug=config.debug_mode)
```

### 3. 모니터링

```python
# monitoring.py
import time
from prometheus_client import Counter, Histogram

# 메트릭 정의
request_counter = Counter(
    'agent_requests_total',
    'Total agent requests',
    ['agent_type', 'status']
)

request_duration = Histogram(
    'agent_request_duration_seconds',
    'Agent request duration',
    ['agent_type']
)

async def monitored_execution(agent_type: str, func):
    """모니터링이 추가된 실행"""

    start_time = time.time()

    try:
        result = await func()
        status = "success" if result.get("success") else "failure"
        request_counter.labels(agent_type=agent_type, status=status).inc()
        return result

    except Exception as e:
        request_counter.labels(agent_type=agent_type, status="error").inc()
        raise

    finally:
        duration = time.time() - start_time
        request_duration.labels(agent_type=agent_type).observe(duration)
```

---

## 📚 더 많은 예제

### examples/ 디렉토리

```
examples/
├── data_collector/
│   ├── langgraph_example.py     # DataCollector 예제
│   └── a2a_example.py           # A2A 버전 예제
├── analysis/
│   ├── langgraph_example.py     # Analysis 예제
│   └── a2a_example.py
├── trading/
│   ├── langgraph_example.py     # Trading 예제
│   └── a2a_example.py
├── supervisor/
│   ├── langgraph_example.py     # Supervisor 예제
│   └── a2a_example.py
└── run_integration_tests.py     # 통합 테스트
```

### 실행 방법

```bash
# DataCollector 예제
python examples/data_collector/langgraph_example.py

# Analysis 예제
python examples/analysis/langgraph_example.py

# Trading 예제
python examples/trading/langgraph_example.py

# Supervisor 예제
python examples/supervisor/langgraph_example.py

# 통합 테스트
python examples/run_integration_tests.py
```

---

## 🎓 학습 리소스

### 공식 문서
- [LangGraph Tutorials](https://langchain-ai.github.io/langgraph/tutorials/)
- [A2A Protocol Spec](../docs/a2a-python_0.3.0.txt)
- [FastMCP Documentation](../docs/fastmcp_2.11.3_llms-full.txt)

### 프로젝트 문서
- [전체 아키텍처 분석](PROJECT_ANALYSIS.md)
- [코드 인덱스](../src/code_index.md)
- [MCP 서버 가이드](../src/mcp_servers/code_index.md)

---

## 💡 자주 묻는 질문 (FAQ)

### Q1: API 키가 없어도 실행할 수 있나요?
A: OPENAI_API_KEY는 필수입니다. 다른 API 키들은 선택사항이며, 없으면 해당 기능만 제한됩니다.

### Q2: 실제 거래는 어떻게 하나요?
A: 키움증권 API 키를 설정하고 trading_domain을 실제 모드로 전환해야 합니다. 기본적으로 Mock 모드로 동작합니다.

### Q3: 에이전트 실행이 느려요
A:
- LLM 모델을 gpt-4o-mini로 변경
- 불필요한 도구 호출 제거
- 병렬 실행 활용
- 캐싱 적용

### Q4: 에러가 발생했어요
A:
1. 로그 확인: `is_debug=True` 설정
2. API 키 확인
3. MCP 서버 상태 확인
4. 네트워크 연결 확인

---

**작성일**: 2025-11-18
**버전**: 1.0.0
