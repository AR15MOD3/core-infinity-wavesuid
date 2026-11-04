"""Scoped entry: acquire data lease before importing any upstream module."""
import os

from scope_identity import DataLease, install_middleware


def main():
    with DataLease():
        from rover.work_owner import configure
        configure()
        from rover.app_life import app
        install_middleware(app, os.environ['ROVER_INSTANCE_TOKEN'])

        from rover.main import main as upstream_main
        upstream_main()


if __name__ == '__main__':
    main()
