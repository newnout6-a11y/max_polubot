@echo off
title MAX Polubot - Deployment Helper
echo ===================================================
echo MAX Polubot - Easy Deployment to Hugging Face Spaces
echo ===================================================
echo.

:: Проверка установлен ли Git
where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Git не установлен или не добавлен в PATH!
    echo Скачайте и установите Git с сайта: https://git-scm.com/
    echo После установки перезапустите этот скрипт.
    pause
    exit /b
)

:: Запрос Git-ссылки на Space
echo Укажите Git URL вашего Space на Hugging Face.
echo (Его можно найти на странице Space: Clone repository -> HTTPS URL)
echo Пример: https://huggingface.co/spaces/username/space-name
echo.
set /p SPACE_URL="Вставьте URL: "

if "%SPACE_URL%"=="" (
    echo [ERROR] Ссылка не может быть пустой!
    pause
    exit /b
)

:: Инициализация Git-репозитория если его нет
if not exist .git (
    echo [*] Инициализация Git-репозитория...
    git init
    git branch -M main
)

:: Настройка удаленного репозитория
echo [*] Подключение к Hugging Face...
git remote remove origin >nul 2>nul
git remote add origin %SPACE_URL%

:: Добавление файлов в коммит
echo [*] Подготовка файлов к коммиту...
git add .

echo [*] Создание коммита...
git commit -m "Deploy MAX Polubot to Hugging Face"

:: Пуш в облако
echo.
echo ====================================================================
echo [*] Отправка кода на Hugging Face...
echo.
echo [ВАЖНО] Git попросит авторизацию.
echo 1. В качестве Username укажите ваш логин Hugging Face.
echo 2. В качестве Password укажите ваш Access Token с правами WRITE.
echo    Получить токен можно тут: https://huggingface.co/settings/tokens
echo ====================================================================
echo.

git push origin main --force

if %errorlevel% eq 0 (
    echo.
    echo ===================================================
    echo [УСПЕХ] Код успешно отправлен на Hugging Face!
    echo Теперь настройте Secrets в панели Space (Settings).
    echo ===================================================
) else (
    echo.
    echo [ОШИБКА] Не удалось отправить код. 
    echo Убедитесь в правильности токена Hugging Face и попробуйте снова.
)
pause
