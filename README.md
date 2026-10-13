# Agente gestor de Mail personal
Agente con inteligencia artificial integrada que permite gestionar la bandeja de un correo electronico.
Es capaz de leer correos y tomar decisiones sobre ellos sin la necesidad de intervención humana.
Permite reducir la carga de los usuarios cuyas bandejas de entrada se ven completamente saturadas
o no quieren estar pendientes del correo constantemente.

## Cómo funciona
En cada ejecución el agente:

1. Se conecta por IMAP y abre la carpeta vigilada (`WATCHED_FOLDER`, por defecto `INBOX`).
2. Busca los correos **no leídos** que llegaron después del último UID procesado. Los correos que el usuario ya ha leído se saltan: se entiende que ya se ha encargado de ellos.
3. Envía cada correo a un modelo de lenguaje (Claude, de Anthropic) junto con las reglas de `rules.md`, que forman el prompt de sistema.
4. El modelo elige una única acción por correo:

   | Acción | Qué hace |
   | --- | --- |
   | `none` | Deja el correo como está. Es la opción por defecto ante la duda. |
   | `mark_as_read` | Marca el correo como leído. |
   | `move` | Mueve el correo a una de las carpetas permitidas (`ALLOWED_FOLDERS`). |
   | `trash` | Mueve el correo a la papelera. Se puede recuperar. |
   | `create_draft` | Guarda un borrador de respuesta al remitente. **Nunca se envía.** |

5. El workflow valida la decisión (por ejemplo, rechaza mover a una carpeta no permitida) y la ejecuta, salvo en modo `DRY_RUN`.
6. Guarda el UID de cada correo procesado en `state.json`, para continuar desde ahí en la siguiente ejecución.

Cada ejecución procesa como máximo `BATCH_SIZE` correos. El agente no borra correos de forma permanente ni envía nada.

### Primera ejecución
Si no existe estado previo para la carpeta (o su `UIDVALIDITY` ha cambiado), **no se procesa ningún correo**: se guarda el correo más reciente como punto de partida y, desde ahí, solo se tratan los que lleguen después. Para volver a empezar desde el momento actual basta con borrar `state.json`.

## Requisitos
- Python 3.12 o superior.
- Una cuenta de correo con acceso IMAP y soporte del comando `MOVE`.
- Una API key de Anthropic.

## Instalación
```bash
python -m venv .venv
```
```bash
.venv\Scripts\activate
```
```bash
pip install anthropic python-dotenv
```

## Configuración
La configuración se lee de variables de entorno o de un archivo `.env` en la raíz del proyecto. Las variables definidas en el entorno (por ejemplo, en `.vscode/launch.json`) **tienen prioridad** sobre el `.env`.

### IMAP
| Variable | Obligatoria | Por defecto | Descripción |
| --- | --- | --- | --- |
| `IMAP_HOST` | Sí | | Servidor IMAP. |
| `IMAP_USERNAME` | Sí | | Usuario (normalmente la dirección de correo). |
| `IMAP_PASSWORD` | Sí | | Contraseña. Solo se lee al conectar. |
| `IMAP_USE_SSL` | No | `true` | Usar conexión SSL. |
| `IMAP_PORT` | No | `993` con SSL, `143` sin SSL | Puerto. |
| `IMAP_TIMEOUT` | No | `30` | Tiempo de espera en segundos. |

### Workflow
| Variable | Obligatoria | Por defecto | Descripción |
| --- | --- | --- | --- |
| `WATCHED_FOLDER` | No | `INBOX` | Carpeta que vigila el agente. |
| `ALLOWED_FOLDERS` | No | (vacío) | Carpetas a las que se puede mover, separadas por comas. Sin carpetas, la acción `move` no se ofrece al modelo. |
| `STATE_PATH` | No | `data/state.json` | Archivo donde se guarda el último UID procesado. |
| `BATCH_SIZE` | No | `20` | Máximo de correos por ejecución. |
| `DRY_RUN` | No | `true` | Si es `true`, las decisiones se registran en el log pero no se ejecutan. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL`. |

### Agente y modelo
| Variable | Obligatoria | Por defecto | Descripción |
| --- | --- | --- | --- |
| `LLM_API_KEY` | Sí | | API key de Anthropic. Solo se lee al crear el cliente. |
| `LLM_MODEL` | Sí | | Modelo a usar, por ejemplo `claude-sonnet-5-5`. |
| `LLM_MAX_TOKEN` | No | `8192` | Máximo de tokens de la respuesta. |
| `RULES_PATH` | No | `rules.md` | Archivo con el prompt de sistema. Debe existir y no estar vacío. |
| `MAX_BODY_CHARS` | No | `8000` | Longitud máxima del cuerpo del correo que se envía al modelo. |

Las rutas relativas se resuelven desde la carpeta en la que se ejecuta el programa.

### Nombres de carpetas
Los valores de `ALLOWED_FOLDERS` deben coincidir **exactamente** con los nombres que devuelve el servidor IMAP:

- Incluye la ruta completa con el separador del servidor (por ejemplo `INBOX.Newsletters` en lugar de `Newsletters`).
- Se distinguen mayúsculas y minúsculas.
- Evita tildes y `ñ` en los nombres de carpeta.
- No incluyas la carpeta vigilada: mover un correo a su propia carpeta se rechaza.

La papelera y la carpeta de borradores se detectan automáticamente por sus atributos IMAP (`\Trash`, `\Drafts`); no hace falta configurarlas.

Ejemplo de `.env`:
```
IMAP_HOST=imap.example.com
IMAP_USERNAME=usuario@example.com
IMAP_PASSWORD=...

WATCHED_FOLDER=INBOX
ALLOWED_FOLDERS=INBOX.Notificaciones,INBOX.Newsletters
STATE_PATH=src/gestor_mail/data/state.json
RULES_PATH=src/gestor_mail/data/rules.md
BATCH_SIZE=10
DRY_RUN=true

LLM_API_KEY=...
LLM_MODEL=claude-sonnet-5-5
```

## Reglas del agente (`rules.md`)
El contenido de `rules.md` se envía **tal cual** como prompt de sistema, así que contiene todas las instrucciones del agente: quién es el usuario, qué acciones puede tomar y cuándo, cómo escribir los borradores y cómo protegerse de correos manipuladores. Se escribe en lenguaje natural.

Las acciones y carpetas disponibles no se escriben en el archivo: el modelo las recibe en el esquema de la herramienta que debe llamar, a partir de la configuración.

La carpeta `src/gestor_mail/data/` está en `.gitignore`, porque contiene las reglas personales y el estado. En una instalación nueva hay que crear el archivo de reglas.

## Ejecución
Desde la raíz del proyecto:
```bash
python -m src.gestor_mail.main
```

En VS Code, la configuración `Mail manager (dry run)` de `.vscode/launch.json` ejecuta el agente forzando `DRY_RUN=true` y `LOG_LEVEL=DEBUG`, aunque el `.env` diga otra cosa.

Al terminar, el log muestra un resumen con los correos procesados y las acciones tomadas. Se recomienda revisar varias ejecuciones en modo `DRY_RUN` antes de desactivarlo.

## Estructura del proyecto
```
src/gestor_mail/
├── main.py                 Punto de entrada: carga la configuración y lanza el workflow.
├── config.py               Lectura y validación de las variables de entorno.
├── errors.py               Errores propios (fallos del LLM).
├── agents/
│   ├── agent.py            AgentHelper: pide al modelo una decisión para cada correo.
│   ├── anthropic_client.py Cliente de la API de Anthropic.
│   └── decision.py         Acciones y decisiones posibles.
├── clients/
│   └── imap_client.py      Cliente IMAP de bajo nivel.
├── models/                 Correos, carpetas y construcción del prompt.
├── services/
│   └── mail_service.py     Operaciones de alto nivel sobre el correo.
├── storage/
│   └── state_repository.py Guarda el último UID procesado por carpeta.
└── workflow/
    └── inbox_agent_workflow.py  Orquesta una ejecución completa.
```

## Seguridad
- El contenido de los correos se trata siempre como datos, nunca como instrucciones para el modelo.
- El agente no envía correos ni borra de forma permanente: las respuestas quedan como borradores y la papelera se puede recuperar.
- Las credenciales solo se leen en el momento de usarlas y nunca deben subirse al repositorio (`.env` está en `.gitignore`).

Author: Alvaro Gomez.
