@echo off
setlocal
cd /d C:\pyfluent
if not defined GIT_BASH_EXE set "GIT_BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%GIT_BASH_EXE%" (
    echo FAIL: Git Bash not found at "%GIT_BASH_EXE%". Set GIT_BASH_EXE.
    exit /b 1
)
"%GIT_BASH_EXE%" --login "C:/pyfluent/scripts/host/pending_checks.sh" %*
exit /b %ERRORLEVEL%
