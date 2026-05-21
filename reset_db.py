from __future__ import annotations

from database import get_db_path, reset_database


def main() -> None:
    db_path = get_db_path()
    reset_database()
    print(f"数据库已重置：{db_path}")
    print("下次启动应用时会自动重新创建数据库和表结构。")


if __name__ == "__main__":
    main()

