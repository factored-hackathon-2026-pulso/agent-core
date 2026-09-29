from agent_core.domain.errors import PROBLEM_STATUS, DomainError, EngineError, InvalidRuntimeRef, ProblemCode


def test_every_problem_code_has_status() -> None:
    assert set(PROBLEM_STATUS) == set(ProblemCode)


def test_status_by_family() -> None:
    assert PROBLEM_STATUS[ProblemCode.credentials_invalid] == 401
    assert PROBLEM_STATUS[ProblemCode.delegation_mismatch] == 403
    assert PROBLEM_STATUS[ProblemCode.agent_forbidden] == 403
    assert PROBLEM_STATUS[ProblemCode.not_found] == 404
    assert PROBLEM_STATUS[ProblemCode.idempotency_conflict] == 409
    assert PROBLEM_STATUS[ProblemCode.run_closed] == 410
    assert PROBLEM_STATUS[ProblemCode.invalid_request] == 422
    assert PROBLEM_STATUS[ProblemCode.cost_budget_exceeded] == 429
    assert PROBLEM_STATUS[ProblemCode.internal_error] == 500


def test_engine_error_carries_code_and_status() -> None:
    err = EngineError(ProblemCode.run_closed, "el run fue escalado")
    assert err.code is ProblemCode.run_closed
    assert err.status == 410
    assert err.detail == "el run fue escalado"


def test_domain_errors_are_not_engine_errors() -> None:
    assert issubclass(InvalidRuntimeRef, DomainError)
    assert not issubclass(InvalidRuntimeRef, EngineError)
