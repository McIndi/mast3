@echo off
REM The mast command of this installation: mast ROOT [ARGUMENTS ...]
setlocal

SET "MAST_HOME=%~dp0"

cd /d "%MAST_HOME%"
call "%MAST_HOME%set-env.bat"

"%MAST_HOME%python\%PYTHON_VERSION%\python" -m mast %*
REM Return the exit status of Python. The variable on the last line is
REM expanded before endlocal runs, so its value survives endlocal.
SET MAST_EXIT_CODE=%ERRORLEVEL%
endlocal & exit /b %MAST_EXIT_CODE%
