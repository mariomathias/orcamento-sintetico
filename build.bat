@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Build - OrcamentoApp
cd /d "%~dp0"

echo ==========================================
echo       GERADOR DO EXECUTAVEL
echo           OrcamentoApp
echo ==========================================
echo.

REM ============================================================
REM CONFIGURACOES
REM ============================================================

set "APP_NAME=OrcamentoApp"
set "MAIN_FILE=main.py"

set "DB_SEINFRA=composicoes_detalhado_atualizado_com_unidades.db"
set "DB_SINAPI=sinapi_2026_01_com_desoneracao_categorias.db"

set "VENV_DIR=venv"
set "BUILD_DIR=build"
set "DIST_DIR=dist"
set "RELEASE_DIR=release\%APP_NAME%"

REM ============================================================
REM VERIFICACOES
REM ============================================================

if not exist "%MAIN_FILE%" (
    echo [ERRO] O arquivo "%MAIN_FILE%" nao foi encontrado.
    echo.
    echo Coloque este build.bat na mesma pasta do main.py.
    echo.
    pause
    exit /b 1
)

if not exist "%DB_SEINFRA%" (
    echo [ERRO] O banco SEINFRA nao foi encontrado:
    echo "%DB_SEINFRA%"
    echo.
    pause
    exit /b 1
)

if not exist "%DB_SINAPI%" (
    echo [ERRO] O banco SINAPI nao foi encontrado:
    echo "%DB_SINAPI%"
    echo.
    pause
    exit /b 1
)

where py >nul 2>&1
if %errorlevel%==0 (
    set "PY_CMD=py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PY_CMD=python"
    ) else (
        echo [ERRO] Python nao foi encontrado neste computador.
        echo Instale o Python antes de gerar o executavel.
        echo.
        pause
        exit /b 1
    )
)

REM ============================================================
REM AMBIENTE VIRTUAL
REM ============================================================

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/6] Criando ambiente virtual...
    %PY_CMD% -m venv "%VENV_DIR%"

    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel criar o ambiente virtual.
        pause
        exit /b 1
    )
) else (
    echo [1/6] Ambiente virtual ja existe.
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

REM ============================================================
REM DEPENDENCIAS
REM ============================================================

echo [2/6] Atualizando pip...
"%VENV_PY%" -m pip install --upgrade pip

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao atualizar o pip.
    pause
    exit /b 1
)

echo.
echo [3/6] Instalando dependencias...
"%VENV_PY%" -m pip install pyinstaller openpyxl

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao instalar as dependencias.
    pause
    exit /b 1
)

REM ============================================================
REM LIMPEZA DO BUILD ANTERIOR
REM ============================================================

echo.
echo [4/6] Limpando arquivos antigos...

if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

if exist "%RELEASE_DIR%" (
    REM NUNCA apagar usuario.db automaticamente.
    if exist "%RELEASE_DIR%\usuario.db" (
        echo Preservando usuario.db existente...
        copy /y "%RELEASE_DIR%\usuario.db" "%TEMP%\%APP_NAME%_usuario_backup.db" >nul
    )

    rmdir /s /q "%RELEASE_DIR%"
)

mkdir "%RELEASE_DIR%" >nul 2>&1

REM ============================================================
REM GERAR EXECUTAVEL
REM ============================================================

echo.
echo [5/6] Gerando executavel...

set "ICON_ARG="
if exist "icone.ico" (
    echo Icone encontrado: icone.ico
    set "ICON_ARG=--icon=icone.ico"
)

"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "%APP_NAME%" ^
    !ICON_ARG! ^
    "%MAIN_FILE%"

if errorlevel 1 (
    echo.
    echo [ERRO] O PyInstaller nao conseguiu gerar o executavel.
    pause
    exit /b 1
)

REM ============================================================
REM MONTAR PASTA PARA DISTRIBUICAO
REM ============================================================

echo.
echo [6/6] Montando pasta de distribuicao...

copy /y "%DIST_DIR%\%APP_NAME%.exe" "%RELEASE_DIR%\%APP_NAME%.exe" >nul
copy /y "%DB_SEINFRA%" "%RELEASE_DIR%\%DB_SEINFRA%" >nul
copy /y "%DB_SINAPI%" "%RELEASE_DIR%\%DB_SINAPI%" >nul

REM Restaura usuario.db se ja existia dentro da pasta release.
if exist "%TEMP%\%APP_NAME%_usuario_backup.db" (
    copy /y "%TEMP%\%APP_NAME%_usuario_backup.db" "%RELEASE_DIR%\usuario.db" >nul
    del /q "%TEMP%\%APP_NAME%_usuario_backup.db" >nul 2>&1
)

echo.
echo ==========================================
echo BUILD CONCLUIDO COM SUCESSO
echo ==========================================
echo.
echo Pasta pronta para distribuicao:
echo "%CD%\%RELEASE_DIR%"
echo.
echo Conteudo esperado:
echo   %APP_NAME%.exe
echo   %DB_SEINFRA%
echo   %DB_SINAPI%
echo.
echo O usuario.db sera criado automaticamente
echo pelo aplicativo na primeira execucao,
echo caso ainda nao exista.
echo.
echo IMPORTANTE:
echo Em atualizacoes futuras, nao substitua nem
echo exclua o usuario.db do cliente.
echo.
pause

endlocal
