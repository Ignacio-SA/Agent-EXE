# 🔨 Guía de Compilación — SmartIA.exe

Instrucciones para generar el ejecutable `SmartIA.exe` a partir del código fuente usando **PyInstaller**.

---

## Requisitos previos

| Herramienta | Versión mínima | Verificar con |
|---|---|---|
| Python | 3.12.x | `python --version` |
| pip | 25.x | `pip --version` |

> **⚠️ Importante:** Usar el **mismo entorno Python** en el que estarán instaladas las dependencias del proyecto. No mezclar versiones ni entornos virtuales a medias.

---

## 1. Clonar / ubicarse en el proyecto

```powershell
cd C:\Users\<TU_USUARIO>\Documents\SmartFran\Agent-EXE
```

---

## 2. Instalar dependencias + PyInstaller

Instalar todas las librerías del proyecto **y** PyInstaller en un solo comando:

```powershell
pip install -r requirements.txt pyinstaller
```

Verificar que PyInstaller quedó instalado:

```powershell
pyinstaller --version
```

---

## 3. Generar el ícono `.ico` (solo la primera vez)

El ícono se genera con Pillow a partir del diseño Smart-IA. Correr antes del build:

```powershell
python make_icon.py
```

Resultado esperado:
```
Ícono generado: ...\ui_test\Nacho.ico  (7 resoluciones)
```

> Si `ui_test/Nacho.ico` ya existe, este paso puede omitirse.

---

## 4. Compilar el ejecutable

Usar el archivo de configuración `.spec` incluido en el proyecto:

```powershell
pyinstaller smartia.spec
```

El proceso toma entre **30 y 90 segundos**. Al finalizar debe mostrar:

```
Building EXE from EXE-00.toc completed successfully.
Build complete! The results are available in: ...\dist
```

### Salida generada

```
Agent-EXE/
├── build/          ← archivos intermedios de PyInstaller (ignorar)
└── dist/
    └── SmartIA.exe ← ejecutable final (~28 MB)
```

---

## 5. Configurar el entorno de ejecución (carpeta `/dist`)

El ejecutable busca el archivo `.env` **en la misma carpeta donde vive el `.exe`**.

### 5.1 Crear el archivo `.env`

Copiar la plantilla y completar los valores:

```powershell
Copy-Item .env.example dist\.env
notepad dist\.env
```

Contenido a completar en `dist\.env`:

```env
# Clave de API de Anthropic (Claude)
ANTHROPIC_API_KEY=sk-ant-...

# Conexión a SQL Server / Microsoft Fabric
DB_SERVER=tu-servidor.database.windows.net
DB_NAME=nombre_de_la_base
DB_USER=usuario@dominio.com
DB_AUTH_MODE=interactive   # opciones: interactive, sql

# Solo si DB_AUTH_MODE=sql
DB_PASSWORD=tu_contraseña

# Ruta de la base de datos local (se crea automáticamente)
MEMORY_DB_PATH=./data/memory.db
```

### 5.2 Estructura esperada en `dist/` antes de ejecutar

```
dist/
├── SmartIA.exe
└── .env              ← copiado y configurado en el paso anterior
```

---

## 6. Primera ejecución

```powershell
.\dist\SmartIA.exe
```

Al arrancar, la aplicación:

1. Lee el `.env` de su carpeta
2. Crea automáticamente la carpeta `data/` con los archivos persistentes
3. Levanta el servidor en `http://127.0.0.1:8000`
4. Abre el navegador en la interfaz de chat
5. Añade un ícono en la **bandeja del sistema** (junto al reloj)

### Estructura `dist/` después de la primera ejecución

```
dist/
├── SmartIA.exe
├── .env
└── data/
    ├── memory.db         ← historial de sesiones (SQLite)
    ├── training_log.md   ← log de feedback para entrenamiento
    └── logs/             ← logs de sesión por fecha
```

---

## 7. Recompilar tras cambios en el código

Basta con volver a correr:

```powershell
pyinstaller smartia.spec
```

> PyInstaller detecta los cambios y regenera solo lo necesario gracias al cache en `build/`. Si hay problemas, limpiar con:
> ```powershell
> Remove-Item -Recurse -Force build, dist
> pyinstaller smartia.spec
> ```

---

## Solución de problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `ModuleNotFoundError: No module named 'dotenv'` | Dependencias no instaladas | Correr `pip install -r requirements.txt pyinstaller` |
| El ícono no aparece en el `.exe` | Falta `ui_test/Nacho.ico` | Correr `python make_icon.py` antes del build |
| El servidor no levanta en 30 s | Puerto 8000 ocupado o falta `.env` | Cerrar otras apps en el puerto o revisar `.env` |
| Ventana de consola negra al abrir | `console=True` en el `.spec` (modo debug) | Cambiar a `console=False` y recompilar para producción |
| Error de conexión a SQL Server | Credenciales incorrectas en `.env` | Verificar con `python validate_setup.py` |

---

## Validar el entorno antes de compilar

# Para generar el .exe
pyinstaller smartia.spec

# Para generar el .exe y limpiar los archivos de build
Remove-Item -Recurse -Force build, dist; pyinstaller smartia.spec


Para asegurarse de que las credenciales y dependencias son correctas antes de generar el `.exe`:

```powershell
python validate_setup.py
```

Todos los ítems deben mostrar `[OK]` para garantizar que la app funciona correctamente al ejecutarse.
