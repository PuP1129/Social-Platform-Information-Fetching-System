# Development environment

This project is developed on Windows and uses the repository-local virtual environment:

`.venv\Scripts\python.exe`

Always run Python commands using the explicit interpreter path. Do not assume that the shell has activated the virtual environment.

Examples:

```powershell
.\.venv\Scripts\python.exe -m compileall src tests main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --help
```
