from agent_core.registry.errors import HTTP_STATUS, IntegrityError, RegistryError, RegistryErrorCode


def test_every_code_has_http_status() -> None:
    assert set(HTTP_STATUS) == set(RegistryErrorCode)
    assert HTTP_STATUS[RegistryErrorCode.validation_failed] == 422
    assert HTTP_STATUS[RegistryErrorCode.forbidden_role] == 403
    assert HTTP_STATUS[RegistryErrorCode.integrity_error] == 500
    assert HTTP_STATUS[RegistryErrorCode.not_found] == 404
    for code in ("gate_failed", "proposal_stale", "candidate_changed", "illegal_transition"):
        assert HTTP_STATUS[RegistryErrorCode(code)] == 409


def test_error_carries_code_detail_and_payload() -> None:
    err = RegistryError(RegistryErrorCode.gate_failed, "no pasa", payload={"x": 1})
    assert (err.code, err.detail, err.payload) == (RegistryErrorCode.gate_failed, "no pasa", {"x": 1})
    assert IntegrityError("blob").code is RegistryErrorCode.integrity_error


def test_new_codes_have_http_status() -> None:
    assert HTTP_STATUS[RegistryErrorCode.idempotency_conflict] == 409
    assert HTTP_STATUS[RegistryErrorCode.quota_exceeded] == 429
