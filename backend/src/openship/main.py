"""OpenShip main entry point."""
from openship.app import create_app
import uvicorn

app = create_app()


def main():
    """Run the OpenShip server."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()