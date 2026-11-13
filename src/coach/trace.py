import time


def log(state, node: str, **info):
    """노드 실행 로그 1줄 추가. 반환값을 노드 업데이트 dict 에 그대로 넣는다."""
    return (state.get("trace") or []) + [{"node": node, "t": round(time.time(), 3), **info}]
