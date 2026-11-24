@echo off
setlocal

SET "MAST_HOME=%~dp0"

cd /d "%MAST_HOME%"

call "%MAST_HOME%mast.bat" -m mast.datapower.system %*
exit /b %ERRORLEVEL%
