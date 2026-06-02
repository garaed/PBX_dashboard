#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Полная установка: 3dProxy + PBX Dashboard
# Запуск:
#   bash <(curl -fsSL https://raw.githubusercontent.com/garaed/PBX_dashboard/main/setup-all.sh)
# ─────────────────────────────────────────────────────────────────────────────
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()   { echo -e "${YELLOW}  ▸${NC} $1"; }
ok()     { echo -e "${GREEN}  ✓${NC} $1"; }
err()    { echo -e "${RED}  ✗${NC} $1"; exit 1; }

[ "$EUID" -eq 0 ] || err "Запустите от root"
command -v curl &>/dev/null || apt-get install -y curl -qq

# ══════════════════════════════════════════════════════════════════════════════
# ЛОГОТИП — замени текст внутри на своё название
# Сгенерировать новый: https://patorjk.com/software/taag (шрифт: ANSI Shadow)
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}${BOLD}"
echo '  ██████╗ ██████╗  ██████╗ ██╗  ██╗██╗   ██╗'
echo '  ██╔══██╗██╔══██╗██╔═══██╗╚██╗██╔╝╚██╗ ██╔╝'
echo '  ██████╔╝██████╔╝██║   ██║ ╚███╔╝  ╚████╔╝ '
echo '  ██╔═══╝ ██╔══██╗██║   ██║ ██╔██╗   ╚██╔╝  '
echo '  ██║     ██║  ██║╚██████╔╝██╔╝ ██╗   ██║   '
echo '  ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝  '
echo -e "${NC}"
echo -e "  ${CYAN}Server Setup — 3dProxy + PBX Dashboard${NC}"
echo ""
# ══════════════════════════════════════════════════════════════════════════════

[ "$EUID" -eq 0 ] || err "Запустите от root"

# ── Количество прокси ─────────────────────────────────────────────────────────
read -rp "  Сколько SOCKS5 прокси создать? " PROXY_COUNT
[[ "$PROXY_COUNT" =~ ^[0-9]+$ ]] && [ "$PROXY_COUNT" -ge 1 ] || err "Введите число ≥ 1"
echo ""

# ── Шаг 1: 3dProxy ───────────────────────────────────────────────────────────
info "Шаг 1/2 — Устанавливаем 3dProxy (${PROXY_COUNT} шт)..."

# Передаём количество через stdin (скрипт использует read с таймаутом 10 сек)
echo "$PROXY_COUNT" | curl -sSL \
    https://raw.githubusercontent.com/garaed/3dProxy/refs/heads/main/3proxy_install | bash

ok "3dProxy установлен"
echo ""

# ── Шаг 2: PBX Dashboard ─────────────────────────────────────────────────────
info "Шаг 2/2 — Устанавливаем PBX Dashboard..."
bash <(curl -fsSL \
    https://raw.githubusercontent.com/garaed/PBX_dashboard/main/setup.sh)
ok "PBX Dashboard установлен"
echo ""

# ── Итог ─────────────────────────────────────────────────────────────────────

# Внешний IP — берём так же как 3proxy (через curl, не hostname)
SERVER_IP=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null || \
            curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
            hostname -I | awk '{print $1}')

CFG="/etc/3proxy/3proxy.cfg"

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}   Установка завершена!${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════${NC}"
echo ""

# PBX Dashboard
echo -e "${CYAN}  📊 PBX Dashboard:${NC}"
echo -e "  http://${SERVER_IP}:8080"
echo ""

# Прокси — парсим конфиг 3proxy
echo -e "${CYAN}  🔒 Прокси (${PROXY_COUNT} шт):${NC}"

if [ ! -f "$CFG" ]; then
    echo -e "  ${RED}Конфиг не найден: $CFG${NC}"
else
    # Извлекаем пары login:pass из строки "users login:CL:pass ..."
    declare -A CREDS
    while IFS= read -r line; do
        [[ "$line" =~ ^users ]] || continue
        for entry in ${line#users }; do
            login=$(cut -d: -f1 <<< "$entry")
            pass=$(cut  -d: -f3 <<< "$entry")
            CREDS["$login"]="$pass"
        done
    done < "$CFG"

    # Сопоставляем: allow <user> → socks -p<port>
    COUNT=0
    current_user=""
    while IFS= read -r line; do
        line="${line%%#*}"   # убираем комментарии
        line="${line//[$'\t' ]/ }"
        line="${line## }"
        line="${line%% }"

        if [[ "$line" =~ ^allow[[:space:]]+([^[:space:]]+) ]]; then
            current_user="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^socks[[:space:]]+-p([0-9]+) ]] && [ -n "$current_user" ]; then
            port="${BASH_REMATCH[1]}"
            pass="${CREDS[$current_user]:-???}"
            echo -e "  ${YELLOW}socks5://${current_user}:${pass}@${SERVER_IP}:${port}${NC}"
            ((COUNT++)) || true
            current_user=""
        fi
    done < "$CFG"

    echo ""
    echo -e "  Создано прокси: ${GREEN}${COUNT}${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════${NC}"
echo ""
