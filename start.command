#!/bin/zsh
# Двойной клик по этому файлу в Finder = перезапуск бота.
# Сначала останавливает старый экземпляр (если запущен), потом запускает свежий.
# Чтобы просто остановить — закрой окно Терминала, которое откроется.
cd "/Users/maksartclip/Desktop/Git All-bot/2026-06-travel-hunter"

echo "Проверяю, не запущен ли уже бот…"
if pgrep -f "[b]ot.py" >/dev/null; then
  echo "  нашёл запущенный — останавливаю."
  pkill -f "[b]ot.py"
  sleep 2
else
  echo "  не запущен."
fi

echo "Запускаю travel-hunter… (закрой это окно, чтобы остановить)"
python3 bot.py
