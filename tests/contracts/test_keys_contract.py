"""Contrato de `KeyProvider`: mismas aserciones contra FakeKeyProvider y EnvKeyProvider, más las pruebas de
seguridad propias del adaptador de entorno. Todo el material de clave aquí es sintético."""

import base64

import pytest

from agent_core.adapters.env_keys import EnvKeyProvider, KeyConfigError
from agent_core.ports import KeyProvider, KeyPurpose
from testing.fakes.keys import FakeKeyProvider, synthetic_key


def _b64(n: int, size: int = 32) -> str:
    return base64.b64encode(bytes([n]) * size).decode()


FP = "AGENTCORE_KEYS_FINGERPRINT"
TM = "AGENTCORE_KEYS_TOKEN_MAP"
ENV = {FP: f"fp-2:{_b64(2)},fp-1:{_b64(1)}", TM: f"tm-1:{_b64(9)}"}


def _env(**overrides: str) -> dict[str, str]:
    return {**ENV, **{{"FP": FP, "TM": TM}[k]: v for k, v in overrides.items()}}


def check_purposes_use_distinct_keys(keys: KeyProvider) -> None:
    fp = keys.key(KeyPurpose.fingerprint, keys.current_kid(KeyPurpose.fingerprint))
    tm = keys.key(KeyPurpose.token_map, keys.current_kid(KeyPurpose.token_map))
    assert fp != tm
    assert len(fp) >= 32 and len(tm) >= 32
    assert keys.key(KeyPurpose.fingerprint, keys.current_kid(KeyPurpose.fingerprint)) == fp


def assert_hidden(text: str, key: bytes) -> None:
    forms = (key.hex(), base64.b64encode(key).decode(), base64.urlsafe_b64encode(key).decode())
    assert all(form not in text for form in forms)
    assert repr(key) not in text and repr(key)[2:-1] not in text
    assert key.decode("latin-1") not in text


def check_unknown_kid_raises_without_material(keys: KeyProvider) -> None:
    with pytest.raises(KeyError) as info:
        keys.key(KeyPurpose.fingerprint, "no-existe")
    material = [keys.key(p, keys.current_kid(p)) for p in KeyPurpose]
    for key in material:
        assert_hidden(str(info.value) + repr(info.value), key)


def check_no_key_material_in_repr(keys: KeyProvider) -> None:
    text = repr(keys) + str(keys)
    for purpose in KeyPurpose:
        key = keys.key(purpose, keys.current_kid(purpose))
        assert_hidden(text, key)


@pytest.fixture(params=["fake", "env"])
def keys(request: pytest.FixtureRequest) -> KeyProvider:
    return FakeKeyProvider.default() if request.param == "fake" else EnvKeyProvider(ENV)


def test_purposes_use_distinct_keys(keys: KeyProvider) -> None:
    check_purposes_use_distinct_keys(keys)


def test_unknown_kid_raises(keys: KeyProvider) -> None:
    check_unknown_kid_raises_without_material(keys)


def test_key_material_never_in_repr(keys: KeyProvider) -> None:
    check_no_key_material_in_repr(keys)


def test_unknown_kid_of_another_purpose_raises(keys: KeyProvider) -> None:
    with pytest.raises(KeyError):
        keys.key(KeyPurpose.token_map, keys.current_kid(KeyPurpose.fingerprint))


# --- sanidad negativa: el contrato debe poder fallar ---


class _SameKeyForAll:
    def current_kid(self, purpose: KeyPurpose) -> str:
        return "k"

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        if kid != "k":
            raise KeyError(kid)
        return b"z" * 32


class _ShortKeys(_SameKeyForAll):
    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        return (b"a" if purpose is KeyPurpose.fingerprint else b"b") * 8


class _LeakyRepr(FakeKeyProvider):
    def __repr__(self) -> str:
        return f"Leaky({self.key(KeyPurpose.fingerprint, 'fp-1')!r})"


class _LeakyKeyError(_SameKeyForAll):
    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        if kid != "k":
            raise KeyError(b"z" * 32)
        return b"z" * 32


def test_contract_detects_shared_or_short_keys() -> None:
    with pytest.raises(AssertionError):
        check_purposes_use_distinct_keys(_SameKeyForAll())
    with pytest.raises(AssertionError):
        check_purposes_use_distinct_keys(_ShortKeys())


def test_contract_detects_key_material_in_repr() -> None:
    leaky = _LeakyRepr(
        keys={
            KeyPurpose.fingerprint: {"fp-1": synthetic_key("fp-1")},
            KeyPurpose.token_map: {"tm-1": synthetic_key("tm-1")},
        },
        current={KeyPurpose.fingerprint: "fp-1", KeyPurpose.token_map: "tm-1"},
    )
    with pytest.raises(AssertionError):
        check_no_key_material_in_repr(leaky)


def test_contract_detects_key_material_in_error() -> None:
    with pytest.raises(AssertionError):
        check_unknown_kid_raises_without_material(_LeakyKeyError())


# --- rotación ---


def test_old_kid_still_available_after_rotation() -> None:
    provider = FakeKeyProvider.default()
    old = provider.current_kid(KeyPurpose.fingerprint)
    old_key = provider.key(KeyPurpose.fingerprint, old)
    provider.rotate(KeyPurpose.fingerprint, "fp-nuevo", b"n" * 32)
    assert provider.current_kid(KeyPurpose.fingerprint) == "fp-nuevo"
    assert provider.key(KeyPurpose.fingerprint, old) == old_key
    env = EnvKeyProvider(ENV)
    assert env.current_kid(KeyPurpose.fingerprint) == "fp-2"
    assert env.key(KeyPurpose.fingerprint, "fp-1") == bytes([1]) * 32
    assert env.key(KeyPurpose.fingerprint, "fp-2") == bytes([2]) * 32


# --- FakeKeyProvider ---


def test_fake_default_keys_are_obviously_synthetic() -> None:
    fake = FakeKeyProvider.default()
    for purpose in KeyPurpose:
        assert fake.key(purpose, fake.current_kid(purpose)).startswith(b"FAKE-INSECURE-KEY:")
    assert len(synthetic_key("x")) >= 32


def test_fake_rotate_rejects_short_key_and_kid_reuse() -> None:
    fake = FakeKeyProvider.default()
    with pytest.raises(ValueError):
        fake.rotate(KeyPurpose.fingerprint, "fp-2", b"corta")
    with pytest.raises(ValueError):
        fake.rotate(KeyPurpose.fingerprint, "fp-1", b"n" * 32)
    assert fake.current_kid(KeyPurpose.fingerprint) == "fp-1"


def test_fake_constructor_validates() -> None:
    with pytest.raises(ValueError):
        FakeKeyProvider({KeyPurpose.fingerprint: {"a": b"x" * 32}}, {KeyPurpose.fingerprint: "otro"})
    with pytest.raises(ValueError):
        FakeKeyProvider({KeyPurpose.fingerprint: {"a": b"x"}}, {KeyPurpose.fingerprint: "a"})


# --- EnvKeyProvider: fallar al arrancar (fail closed) ---


def test_env_provider_fails_at_startup_without_keys() -> None:
    with pytest.raises(RuntimeError):
        EnvKeyProvider({})
    with pytest.raises(RuntimeError):
        EnvKeyProvider({**ENV, TM: f"tm-1:{base64.b64encode(b'corta').decode()}"})


@pytest.mark.parametrize("missing", [FP, TM])
def test_env_provider_requires_each_purpose(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(KeyConfigError, match=missing):
        EnvKeyProvider(env)


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_env_provider_rejects_blank_variable(blank: str) -> None:
    with pytest.raises(KeyConfigError):
        EnvKeyProvider(_env(FP=blank))


def test_env_provider_min_length_boundary() -> None:
    with pytest.raises(KeyConfigError):
        EnvKeyProvider(_env(TM=f"tm-1:{_b64(9, 31)}"))
    EnvKeyProvider(_env(TM=f"tm-1:{_b64(9, 32)}"))


@pytest.mark.parametrize(
    "encoded",
    [
        "@@@@" + _b64(9)[4:],  # caracteres fuera del alfabeto: b64decode laxo los descartaría
        _b64(9)[:-1],  # relleno faltante
        _b64(9) + "\n!",  # basura al final
        base64.urlsafe_b64encode(b"\xfb\xff" * 16).decode(),  # alfabeto url-safe, no estándar
        "",
    ],
)
def test_env_provider_base64_is_strict(encoded: str) -> None:
    with pytest.raises(KeyConfigError):
        EnvKeyProvider(_env(TM=f"tm-1:{encoded}"))


@pytest.mark.parametrize(
    "value",
    [
        f":{_b64(9)}",
        f"tm-1:{_b64(9)},",
        f"tm-1:{_b64(9)},,tm-2:{_b64(8)}",
        "tm-1",
        f"tm-1:{_b64(9)},tm-1:{_b64(8)}",
    ],
)
def test_env_provider_rejects_malformed_entries(value: str) -> None:
    with pytest.raises(KeyConfigError):
        EnvKeyProvider(_env(TM=value))


def test_env_provider_rejects_shared_key_material_across_purposes() -> None:
    with pytest.raises(KeyConfigError):
        EnvKeyProvider(_env(TM=f"tm-1:{_b64(1)}"))


def test_env_provider_first_entry_is_current_and_whitespace_is_tolerated() -> None:
    env = _env(FP=f" fp-2 : {_b64(2)} , fp-1:{_b64(1)}")
    provider = EnvKeyProvider(env)
    assert provider.current_kid(KeyPurpose.fingerprint) == "fp-2"
    assert provider.key(KeyPurpose.fingerprint, "fp-2") == bytes([2]) * 32


# --- EnvKeyProvider: no filtra material ---

_CANARY = base64.b64encode(b"CANARY-SECRET-MATERIAL-0123456789")  # 33 bytes válidos
_CANARY_B64 = _CANARY.decode()


def test_env_errors_never_echo_env_values() -> None:
    bad_values = [
        f"tm-1:{_CANARY_B64}!",  # base64 inválido con el canario
        f"tm-1:{_CANARY_B64[:20]}",  # truncado
        f"tm-1:{_CANARY_B64},tm-1:{_CANARY_B64}",  # kid repetido
        f"tm-1:{_b64(1)}",  # comparte material con fingerprint
    ]
    for value in bad_values:
        with pytest.raises(KeyConfigError) as info:
            EnvKeyProvider(_env(TM=value))
        text = str(info.value)
        assert _CANARY_B64[:12] not in text
        assert _b64(1) not in text
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__ or info.value.__context__ is None


def test_env_repr_and_str_hide_material() -> None:
    provider = EnvKeyProvider(_env(TM=f"tm-1:{_CANARY_B64}"))
    text = repr(provider) + str(provider)
    assert _CANARY_B64 not in text
    assert "CANARY" not in text
    assert provider.LABEL in text
    assert "fp-2" in text  # los kid no son secretos


def test_env_unknown_kid_error_has_no_material() -> None:
    provider = EnvKeyProvider(_env(TM=f"tm-1:{_CANARY_B64}"))
    with pytest.raises(KeyError) as info:
        provider.key(KeyPurpose.token_map, "otro")
    assert _CANARY_B64 not in str(info.value)


# --- EnvKeyProvider: el entorno entra solo por inyección o por fábrica explícita ---


def test_env_provider_copies_the_mapping_at_construction() -> None:
    env = dict(ENV)
    provider = EnvKeyProvider(env)
    env.clear()
    assert provider.key(KeyPurpose.fingerprint, "fp-1") == bytes([1]) * 32


def test_env_provider_injected_mapping_ignores_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FP, f"otro:{_b64(5)}")
    monkeypatch.setenv(TM, f"otro:{_b64(6)}")
    assert EnvKeyProvider(ENV).current_kid(KeyPurpose.fingerprint) == "fp-2"


def test_from_environ_reads_process_environment_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FP, f"otro:{_b64(5)}")
    monkeypatch.setenv(TM, f"otro-tm:{_b64(6)}")
    assert EnvKeyProvider.from_environ().current_kid(KeyPurpose.fingerprint) == "otro"


def test_from_environ_fails_closed_when_process_has_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FP, raising=False)
    monkeypatch.delenv(TM, raising=False)
    with pytest.raises(KeyConfigError):
        EnvKeyProvider.from_environ()
