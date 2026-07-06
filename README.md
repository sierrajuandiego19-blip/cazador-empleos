# Cazador de Empleos · Data Analyst / Data Scientist (middle, remoto)

Sistema personal y gratuito que busca ofertas de empleo **remotas** de análisis y ciencia de datos de **nivel junior y middle**, las **verifica contra señales de fraude**, las **almacena sin duplicados** y se **actualiza sola cada 4 horas**. El resultado es un panel web que puedes consultar cuando quieras.

## Por qué es seguro y legal

- **Solo APIs públicas oficiales.** Consulta los endpoints que las propias bolsas de empleo publican para desarrolladores (Get on Board, Remotive, Remote OK, Jobicy). No hay scraping agresivo ni evasión de bloqueos.
- **Respeta los términos de cada fuente.** Remotive pide máximo ~4 consultas diarias: el sistema le aplica un mínimo de 6 horas entre consultas automáticamente. Todas las ofertas enlazan y atribuyen a su publicación original, como exigen Remotive y Remote OK.
- **No maneja tus datos personales.** No pide registro, no guarda tu correo, no envía nada tuyo a ningún lado. Tú siempre postulas directamente en el sitio oficial de la oferta.
- **Filtro anti-fraude.** Cada oferta recibe un índice de confianza de 0 a 100. Se descartan automáticamente (índice < 60) las que muestran patrones de estafa: cobros de "inscripción" o "capacitación", solicitud de cédula o datos bancarios, pagos por Western Union o cripto, contacto solo por WhatsApp/Telegram, promesas de dinero fácil, enlaces acortados, etc. Las descartadas quedan registradas con sus motivos en `data/descartadas_fraude.json`.

## Qué filtra

| Criterio | Regla |
|---|---|
| Rol | Data analyst, data scientist, analytics, BI (en español o inglés) |
| Nivel | Junior y middle / semi senior. Excluye senior, lead y prácticas/pasantías |
| Modalidad | 100% remoto y compatible con Colombia / LATAM (excluye "USA only", "Europe only", etc.) |
| Idioma | Español, o inglés. Si la vacante en inglés parece exigir inglés **hablado** (llamadas, standups, "fluent spoken English"), se marca con la advertencia "Posible inglés hablado" y puedes ocultarlas con un clic |

## Puesta en marcha (una sola vez, ~10 minutos)

Necesitas una cuenta gratuita de GitHub. GitHub Actions ejecutará la búsqueda cada 4 horas y GitHub Pages publicará tu panel, todo gratis.

1. **Crea un repositorio** en GitHub (por ejemplo `cazador-empleos`). Hazlo **público** para que Pages y Actions sean gratuitos e ilimitados. Es seguro: el repositorio solo contiene ofertas públicas, ningún dato tuyo.
2. **Sube estos archivos** al repositorio (arrastrándolos en la web de GitHub o con `git push`), conservando la estructura de carpetas, incluida `.github/workflows/`.
3. **Activa Actions**: pestaña *Actions* → botón para habilitar workflows → abre "Actualizar ofertas cada 4 horas" → *Run workflow* para la primera corrida.
4. **Permite que el bot escriba**: *Settings → Actions → General → Workflow permissions →* marca **Read and write permissions** → *Save*.
5. **Activa el panel web**: *Settings → Pages → Source: Deploy from a branch → Branch: `main`, carpeta `/docs`* → *Save*.

Tu panel quedará disponible para siempre en:

```
https://TU_USUARIO.github.io/cazador-empleos/
```

Guárdalo en favoritos del celular y del computador; se actualiza solo.

## Uso local (opcional)

```bash
pip install -r requirements.txt
python job_hunter.py          # busca, filtra, almacena y regenera docs/index.html
python job_hunter.py --demo   # prueba el sistema sin internet, con ofertas de ejemplo
python job_hunter.py --forzar # ignora los límites de frecuencia (úsalo con moderación)
```

Luego abre `docs/index.html` en tu navegador. Para automatizarlo localmente cada 4 horas: en Linux/Mac agrega a `crontab -e` la línea `0 */4 * * * cd /ruta/al/proyecto && python3 job_hunter.py`; en Windows usa el Programador de tareas.

## Estructura

```
job_hunter.py                        # todo el sistema: fuentes, filtros, almacén y panel
requirements.txt
.github/workflows/actualizar_ofertas.yml   # el "reloj" de cada 4 horas
data/ofertas.json                    # ofertas activas (deduplicadas)
data/historial.json                  # ofertas que expiraron (últimas 500)
data/descartadas_fraude.json         # ofertas rechazadas y por qué
data/estado_fuentes.json             # control de frecuencia por fuente
docs/index.html                      # tu panel (lo sirve GitHub Pages)
```

## Personalización rápida (editando `job_hunter.py`)

- **Agregar/quitar fuentes**: cambia `"activa": True/False` en el diccionario `FUENTES` (Arbeitnow, enfocada en Europa, viene desactivada).
- **Ampliar el rol**: agrega términos a `PALABRAS_ROL` (p. ej. `"data engineer"`).
- **Ajustar el nivel**: los patrones `PATRON_SENIOR` y `PATRON_PRACTICAS` controlan qué se excluye (junior y middle se aceptan).
- **Endurecer o relajar el anti-fraude**: ajusta las penalizaciones en `BANDERAS_ROJAS` o el umbral `confianza < 60` en `procesar_crudas`.

## Reglas de oro al postular (léelas siempre)

1. **Nunca pagues** por una vacante: ni "inscripción", ni "capacitación", ni "kit de trabajo". Empresa que cobra = estafa.
2. **Nunca envíes** cédula, pasaporte ni datos bancarios antes de tener una oferta formal firmada; los datos bancarios solo se entregan al firmar contrato, por canales oficiales de la empresa.
3. **Postula solo desde el botón "Postular en el sitio oficial"** de cada tarjeta; desconfía de reclutadores que te muevan a WhatsApp/Telegram con correos de Gmail.
4. **Verifica la empresa** en 2 minutos: sitio web propio, perfil de LinkedIn con empleados reales, y que el reclutador escriba desde un correo corporativo.
5. Si una entrevista es solo por chat, te "contratan" el mismo día o te presionan con urgencia, retírate.
