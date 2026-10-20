"""Provision the isolated MCPHub database using the existing MariaDB root Secret."""

import os

import pymysql


def main() -> None:
    host = os.environ["MARIADB_HOST"]
    root_password = os.environ["MARIADB_ROOT_PASSWORD"]
    app_password = os.environ["MCPHUB_DB_PASSWORD"]
    connection = pymysql.connect(
        host=host,
        port=3306,
        user="root",
        password=root_password,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS `mcphub` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS 'mcphub_app'@'%%' IDENTIFIED BY %s",
                (app_password,),
            )
            cursor.execute(
                "ALTER USER 'mcphub_app'@'%%' IDENTIFIED BY %s",
                (app_password,),
            )
            cursor.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES "
                "ON `mcphub`.* TO 'mcphub_app'@'%'"
            )
            cursor.execute("FLUSH PRIVILEGES")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
