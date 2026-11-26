# Claude issue proposals / Propuestas de Claude para issues

## English

1. In **Settings → Secrets and variables → Actions**, use the repository secret `ANTHROPIC_API_TOKEN`.
2. In **Actions → Claude issue proposal → Run workflow**, select `main`, enter an issue number and optionally enter comma-separated public source or documentation paths. Leave **execute** off to preview the prompt for free.
3. Review the artifact. With **execute** on, the run makes one Claude Sonnet 5.5 request after a free token count. It reserves input headroom and caps output before checking the USD 5 budget for that run. Download `proposal.md` and `manifest.json` for review and implementation.

For the local CLI, run `python3 scripts/claude_issue_helper.py --issue=71 --files=docs/api.md --output-dir=claude-preview`. The default preview contacts only public GitHub. To generate locally, provide `ANTHROPIC_API_TOKEN` or `ANTHROPIC_API_KEY`, pass `--execute`, and choose a new output directory. Context comes from the public issue and up to six explicitly selected files on public `main`. The manifest records the commit, prompt hash, model, reserved cost and reported token usage when available. Estimates use the published standard [model rates](https://platform.claude.com/docs/en/models/sonnet-5-5/overview).

The workflow produces text for review. The maintainer or local agent applies and tests any accepted changes. This helper is for repository maintenance and runs independently of the paper's research experiments.

## Español

1. En **Settings → Secrets and variables → Actions**, usa el secreto de repositorio `ANTHROPIC_API_TOKEN`.
2. En **Actions → Claude issue proposal → Run workflow**, elige `main`, indica el número de issue y, opcionalmente, rutas públicas de código o documentación separadas por comas. Deja **execute** desactivado para previsualizar el prompt gratis.
3. Revisa el artefacto. Con **execute** activado, el flujo hace una petición a Claude Sonnet 5.5 tras un conteo gratuito. Reserva margen para la entrada y limita la salida antes de comprobar el presupuesto de 5 USD para esa ejecución. Descarga `proposal.md` y `manifest.json` para revisar e implementar la propuesta.

Para usar la CLI local, ejecuta `python3 scripts/claude_issue_helper.py --issue=71 --files=docs/api.md --output-dir=claude-preview`. La vista previa solo consulta GitHub público. Para generar localmente, configura `ANTHROPIC_API_TOKEN` o `ANTHROPIC_API_KEY`, añade `--execute` y elige un directorio de salida nuevo. El contexto procede del issue público y de hasta seis archivos seleccionados en el `main` público. El manifiesto registra el commit, el hash del prompt, el modelo, el coste reservado y el uso de tokens cuando esté disponible. Los cálculos usan las [tarifas estándar](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) publicadas.

El flujo entrega texto para revisión. La persona responsable o el agente local aplica y prueba los cambios que acepte. La herramienta sirve para mantener el repositorio y funciona de forma independiente de los experimentos del artículo.
