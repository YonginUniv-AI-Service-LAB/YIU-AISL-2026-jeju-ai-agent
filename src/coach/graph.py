"""LangGraph 조립(★B 소유). 노드 구현은 nodes/ 의 담당자 파일."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import a_data as A
from .nodes import b_orchestrator as B
from .nodes import c_experiment as C
from .state import CoachState


def build_graph():
    g = StateGraph(CoachState)
    # B
    g.add_node("parse_input", B.parse_input)
    g.add_node("ask_back", B.ask_back)
    g.add_node("review_experiment", B.review_experiment)
    g.add_node("bump_retry", B.bump_retry)
    g.add_node("finish", B.finish)
    # A
    g.add_node("check_fitness", A.check_fitness)
    g.add_node("analyze", A.analyze)
    # C
    g.add_node("design_experiment", C.design_experiment)
    g.add_node("compose_report", C.compose_report)
    g.add_node("verify", C.verify)
    g.add_node("drop_violations", C.drop_violations)

    g.add_edge(START, "parse_input")
    g.add_conditional_edges("parse_input", B.route_after_parse,
                            {"ask_back": "ask_back", "review_experiment": "review_experiment", "check_fitness": "check_fitness"})
    g.add_edge("ask_back", END)
    g.add_edge("review_experiment", "check_fitness")
    g.add_edge("check_fitness", "analyze")
    g.add_edge("analyze", "design_experiment")
    g.add_edge("design_experiment", "compose_report")
    g.add_edge("compose_report", "verify")
    g.add_conditional_edges("verify", B.route_after_verify,
                            {"finish": "finish", "compose_report": "bump_retry", "drop_violations": "drop_violations"})
    g.add_edge("bump_retry", "compose_report")
    g.add_edge("drop_violations", END)
    g.add_edge("finish", END)
    return g.compile()


def run(question: str, store_input: dict | None = None, **extra) -> dict:
    return build_graph().invoke({"question": question, "store_input": store_input or {}, "trace": [], **extra})
