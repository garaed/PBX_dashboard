#!/usr/bin/env python3
"""
pool_setup.py — Инициализация пулов extension для randcool
Читает extensions из FreePBX, распределяет по группам, генерирует init_pools.sh

Использование:
  python3 pool_setup.py                          # интерактивный режим
  python3 pool_setup.py --apply                  # сразу применить к Asterisk
  python3 pool_setup.py --file list.txt          # из файла вместо FreePBX
  python3 pool_setup.py --threshold 80 --apply   # свой порог + сразу применить
"""

import random
import subprocess
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path


# ── Чтение учётных данных FreePBX ────────────────────────────────────────────
def read_freepbx_creds():
    conf = Path('/etc/freepbx.conf')
    if not conf.exists():
        raise FileNotFoundError('/etc/freepbx.conf не найден')
    creds = {}
    for line in conf.read_text().splitlines():
        m = re.match(r"""\$amp_conf\['(AMP\w+)'\]\s*=\s*'([^']*)'""", line)
        if m:
            creds[m.group(1)] = m.group(2)
    return creds


# ── Получение списка extensions из FreePBX MySQL ──────────────────────────────
def get_extensions_from_freepbx():
    try:
        import mysql.connector
    except ImportError:
        print("Установи: pip install mysql-connector-python --break-system-packages")
        sys.exit(1)

    try:
        creds = read_freepbx_creds()
        conn = mysql.connector.connect(
            host='localhost',
            user=creds.get('AMPDBUSER', 'freepbxuser'),
            password=creds.get('AMPDBPASS', ''),
            database=creds.get('AMPDBNAME', 'asterisk'),
            connection_timeout=5
        )
        cursor = conn.cursor()
        # Берём только алфавитные endpoint (trunk-style имена: krenat, kvaele...)
        # Исключаем числовые (группы операторов: 2033, 2015...)
        cursor.execute("""
            SELECT DISTINCT id FROM pjsip
            WHERE type = 'endpoint'
              AND id REGEXP '^[a-z]+$'
              AND LENGTH(id) >= 4
            ORDER BY id
        """)
        extensions = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return extensions
    except Exception as e:
        print(f"Ошибка подключения к FreePBX MySQL: {e}")
        sys.exit(1)


# ── Чтение из файла ───────────────────────────────────────────────────────────
def get_extensions_from_file(path):
    with open(path) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith('#')]


# ── Вывод статуса текущих пулов ───────────────────────────────────────────────
def show_status():
    print("=== Текущие пулы в Asterisk ===")
    r = subprocess.run(['asterisk', '-rx', 'database show glist'],
                       capture_output=True, text=True)
    if r.stdout.strip():
        for line in r.stdout.strip().splitlines():
            if '/glist/' in line:
                parts = line.split(':')
                group = parts[0].strip().split('/')[-1]
                exts = parts[1].strip().split('&') if len(parts) > 1 else []
                print(f"  Группа {group}: {len(exts)} extensions")
    else:
        print("  (пулы не инициализированы)")

    r2 = subprocess.run(['asterisk', '-rx', 'database show cool'],
                        capture_output=True, text=True)
    reserve_line = [l for l in r2.stdout.splitlines() if '/cool/reserve' in l]
    burned = [l for l in r2.stdout.splitlines() if '/gstatus/' in l and 'burned' in l]
    threshold_line = [l for l in r2.stdout.splitlines() if '/cool/threshold' in l]

    if reserve_line:
        reserve_count = len(reserve_line[0].split(':')[-1].strip().split('&'))
        print(f"  Резерв: {reserve_count} extensions")
    if threshold_line:
        thr = threshold_line[0].split(':')[-1].strip()
        print(f"  Порог ротации: {thr} звонков")
    if burned:
        print(f"  Сгоревших extensions: {len(burned)}")


# ── Принудительная ротация сгоревших ─────────────────────────────────────────
def force_rotate(group=None):
    r = subprocess.run(['asterisk', '-rx', 'database show cool'],
                       capture_output=True, text=True)

    # Получаем резерв
    reserve_line = [l for l in r.stdout.splitlines() if '/cool/reserve' in l]
    if not reserve_line:
        print("Резервный пул пуст — ротация невозможна")
        return

    reserve = reserve_line[0].split(':', 1)[-1].strip().split('&')

    # Получаем сгоревшие
    burned = []
    for line in r.stdout.splitlines():
        if '/gstatus/' in line and 'burned' in line:
            name = line.split('/gstatus/')[-1].split(':')[0].strip()
            burned.append(name)

    if not burned:
        print("Нет сгоревших extensions")
        return

    print(f"Сгоревших: {len(burned)}, в резерве: {len(reserve)}")
    rotated = 0

    for burned_ext in burned:
        if not reserve:
            print("Резерв исчерпан")
            break

        new_ext = reserve.pop(0)

        # Найти в каком пуле группы сидит burned_ext
        r2 = subprocess.run(['asterisk', '-rx', 'database show glist'],
                             capture_output=True, text=True)
        for line in r2.stdout.splitlines():
            if '/glist/' in line:
                grp = line.split('/glist/')[-1].split(':')[0].strip()
                exts_str = line.split(':', 1)[-1].strip()
                if burned_ext in exts_str.split('&'):
                    if group and grp != group:
                        continue
                    # Заменяем в пуле
                    new_list = exts_str.replace(burned_ext, new_ext)
                    subprocess.run(['asterisk', '-rx',
                                    f'database put glist {grp} {new_list}'], check=True)
                    subprocess.run(['asterisk', '-rx',
                                    f'database del gstatus {burned_ext}'], check=True)
                    subprocess.run(['asterisk', '-rx',
                                    f'database del gcount {burned_ext}'], check=True)
                    print(f"  {grp}: {burned_ext} → {new_ext}")
                    rotated += 1
                    break

    # Обновляем резерв
    if reserve:
        subprocess.run(['asterisk', '-rx',
                        f'database put cool reserve {"&".join(reserve)}'], check=True)
    else:
        subprocess.run(['asterisk', '-rx', 'database del cool reserve'], check=True)

    print(f"Ротация завершена: заменено {rotated} extensions, осталось в резерве: {len(reserve)}")


# ── Основная логика ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Pool setup для randcool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python3 pool_setup.py                        # интерактивно, генерирует init_pools.sh
  python3 pool_setup.py --apply                # интерактивно + сразу применить
  python3 pool_setup.py --file exts.txt        # список из файла
  python3 pool_setup.py --threshold 80         # свой порог
  python3 pool_setup.py --status               # показать текущие пулы
  python3 pool_setup.py --rotate               # ротация всех сгоревших из резерва
  python3 pool_setup.py --rotate --group 2033  # ротация только для группы 2033
        """
    )
    parser.add_argument('--file', help='Файл со списком extensions (один на строку)')
    parser.add_argument('--threshold', type=int, default=100,
                        help='Порог звонков до ротации (default: 100)')
    parser.add_argument('--cooldown', type=int, default=30,
                        help='Кулдаун после звонка в секундах (default: 30)')
    parser.add_argument('--output', default='init_pools.sh',
                        help='Файл с командами инициализации (default: init_pools.sh)')
    parser.add_argument('--apply', action='store_true',
                        help='Сразу применить к Asterisk (без файла)')
    parser.add_argument('--status', action='store_true',
                        help='Показать текущее состояние пулов')
    parser.add_argument('--rotate', action='store_true',
                        help='Принудительная ротация сгоревших extensions из резерва')
    parser.add_argument('--group', help='Ограничить ротацию одной группой (с --rotate)')
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.rotate:
        force_rotate(group=args.group)
        return

    # ── Получаем список extensions ────────────────────────────────────────────
    if args.file:
        all_extensions = get_extensions_from_file(args.file)
        print(f"Загружено из файла: {len(all_extensions)} extensions")
    else:
        print("Получаю список extensions из FreePBX MySQL...")
        all_extensions = get_extensions_from_freepbx()
        print(f"Найдено в FreePBX: {len(all_extensions)} extensions")

    if not all_extensions:
        print("Список extensions пуст. Проверь FreePBX или укажи --file")
        sys.exit(1)

    pool = all_extensions.copy()
    random.shuffle(pool)

    # ── Интерактивный ввод групп ──────────────────────────────────────────────
    print(f"\nВведи группы в формате НОМЕР:ЛИНИЙ (Enter для завершения)")
    print(f"Всего extensions: {len(pool)}\n")

    groups = {}
    while True:
        try:
            line = input(f"  Группа [{len(pool) - sum(groups.values())} осталось]: ").strip()
        except EOFError:
            break
        if not line:
            break
        try:
            name, count_str = line.split(':')
            count = int(count_str.strip())
            if count <= 0:
                print("  Количество должно быть > 0")
                continue
            groups[name.strip()] = count
            print(f"  ✓ {name.strip()}: {count} extensions")
        except ValueError:
            print("  Формат: NAME:COUNT (например 2033:40)")

    if not groups:
        print("Группы не указаны")
        sys.exit(1)

    total_needed = sum(groups.values())
    if total_needed > len(pool):
        print(f"\nОшибка: нужно {total_needed} extensions, доступно {len(pool)}")
        sys.exit(1)

    # ── Распределение ─────────────────────────────────────────────────────────
    group_pools = {}
    idx = 0
    for name, count in groups.items():
        group_pools[name] = pool[idx:idx + count]
        idx += count
    reserve = pool[idx:]

    # ── Сводка ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*50}")
    print(f"  РАСПРЕДЕЛЕНИЕ EXTENSIONS")
    print(f"{'═'*50}")
    for name, exts in group_pools.items():
        print(f"  Группа {name:<8}: {len(exts):3} extensions")
    print(f"  {'─'*38}")
    print(f"  Резерв        : {len(reserve):3} extensions  ← ротация при сгорании")
    print(f"  Порог ротации : {args.threshold} звонков на extension")
    print(f"  Кулдаун       : {args.cooldown} сек после звонка")
    print(f"{'═'*50}\n")

    # ── Генерация команд ──────────────────────────────────────────────────────
    cmds = [
        f"#!/bin/bash",
        f"# Pool init — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# Extensions: {len(all_extensions)} total | Порог: {args.threshold} | Кулдаун: {args.cooldown}s",
        f"",
        f"# Очищаем предыдущие данные",
        f'asterisk -rx "database deltree glist"',
        f'asterisk -rx "database deltree gcount"',
        f'asterisk -rx "database deltree gstatus"',
        f'asterisk -rx "database del cool reserve"',
        f'asterisk -rx "database del cool threshold"',
        f'asterisk -rx "database del cool cooldown"',
        f"",
        f"# Пулы групп",
    ]
    for name, exts in group_pools.items():
        cmds.append(f'asterisk -rx "database put glist {name} {"&".join(exts)}"')

    cmds += [
        f"",
        f"# Резервный пул ({len(reserve)} extensions)",
    ]
    if reserve:
        cmds.append(f'asterisk -rx "database put cool reserve {"&".join(reserve)}"')
    else:
        cmds.append(f"# (резервный пул пуст)")

    cmds += [
        f"",
        f"# Параметры",
        f'asterisk -rx "database put cool threshold {args.threshold}"',
        f'asterisk -rx "database put cool cooldown {args.cooldown}"',
        f"",
        f"# Проверка",
        f'echo ""',
        f'echo "=== Инициализация завершена ==="',
        f'asterisk -rx "database show glist"',
        f'echo "Резерв: {len(reserve)} extensions"',
    ]

    if args.apply:
        print("Применяю к Asterisk...")
        for cmd in cmds:
            if cmd.startswith('asterisk') or cmd.startswith('echo'):
                subprocess.run(cmd, shell=True)
        print("\nГотово!")
    else:
        output = Path(args.output)
        output.write_text('\n'.join(cmds) + '\n')
        output.chmod(0o755)
        print(f"Сгенерирован: {args.output}")
        print(f"\nПрименить: bash {args.output}")
        print(f"Или сразу: python3 pool_setup.py --apply")


if __name__ == '__main__':
    main()
