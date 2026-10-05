# Compose de referencia de `agentcore serve`

Contrato que infra traslada a EC2: qué servicios, qué entorno y qué archivos monta `serve`.

```bash
cp deploy/compose/.env.example deploy/compose/.env   # y rellénalo (secretos solo aquí)
docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env up --build
```

`deploy/compose/state/` (montado de solo lectura en `/state`) debe traer lo que no es un secreto de entorno:

| Archivo | Contenido |
|---|---|
| `identity-keys.json`, `staff-keys.json` | claves públicas Ed25519 (formato en `docs/serve-env.md` §6) |
| `calibration/` | un `<id>.json` por artefacto de calibración (lo exporta el equipo de datos) |
| `classifier/` | `<ref>.json` en formato `tfidf-logreg-v1` |
| `field-classification.json` | catálogo de clasificación de campos (`field_classification.json` de data-pipeline más el overlay propio) |
| `fx-rates.json` | `{"USD":"1","MXN":"0.055",...}`: tasas FIJAS (USD por unidad) de `convertir_moneda`; sin él la tool falla cerrada |
| `field-grants.json` | `[["campo","purpose"], ...]`; sin él nadie lee campos de clientes |

La imagen: multi-etapa, usuario no root (uid 10001), bases fijadas por digest, `HEALTHCHECK` sobre `/healthz`,
`ENTRYPOINT agentcore` (`serve` por defecto; también `migrate`, `sweep --once`, `relay`).

```bash
docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t agent-core:local .            # arquitectura local
docker buildx build --platform linux/amd64,linux/arm64 -t agent-core:multi .                    # ambas
```

## Prueba local con datos sintéticos

```bash
uv run python scripts/serve_state.py                      # claves de PRUEBA, calibración y clasificador sintéticos, catálogo
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.local.yml \
  --env-file deploy/compose/.env up --build
```

`docker-compose.local.yml` añade el puerto de Postgres para importar el registry desde el host y un
`grants-stub` (DOBLE de la plataforma: toda delegación firmada vale). `serve` corre en `mode=production`: las
siete piezas son reales; lo único sintético son los artefactos de calibración y de clasificador y la plataforma.
Si cambias el código y la imagen no lo recoge, reconstruye con `docker compose build --no-cache migrate`.
