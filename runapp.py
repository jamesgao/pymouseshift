import logging
from mouseshift import app
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    icon = app.App()
    icon.run()