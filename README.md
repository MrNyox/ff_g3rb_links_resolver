طلبية خاصة عشان احمد الحلو
# Project Name

A short description of what this project does.

## Prerequisites

- Python 3.9 or newer
- pip (comes with Python)
- Git

Check your Python version:

```bash
python --version
```

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/MrNyox/ff_g3rb_links_resolver
cd ff_g3rb_links_resolver
```
or you can download the ZIP file and extract it.

### 2. Create a virtual environment

```bash
python -m venv venv
```

### 3. Activate the virtual environment

**macOS / Linux:**

```bash
source venv/bin/activate
```

**Windows (Command Prompt):**

```bat
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
You may use this one most likely if you're on windows 11. 

```powershell
venv\Scripts\Activate.ps1
```

When activated, your terminal prompt will show `(venv)`.

### 4. Install the requirements

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Running the App

```bash
python app.py
```

## Deactivating the Virtual Environment

When you're done working:

```bash
deactivate
```

## Troubleshooting

- **`python` not found:** try `python3` instead (common on macOS/Linux).
- **PowerShell blocks activation:** run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.
- **Dependency errors:** make sure the virtual environment is activated before running `pip install`.
