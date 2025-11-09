# Card Game Pyramid Prototype

This project uses [pygame](https://www.pygame.org/news) for rendering. The script will prompt you to install pygame if it is missing.

## Installing Dependencies

Install pygame with pip:

```bash
python -m pip install --upgrade pip
python -m pip install --upgrade setuptools wheel
python -m pip install pygame
```

If you see an error similar to the following when installing pygame on Windows:

```
ModuleNotFoundError: No module named 'setuptools._distutils.msvccompiler'
```

make sure `setuptools` is installed and up to date. Python 3.14 removes the built-in `distutils` module, so using a current version of `setuptools` is required to provide the replacement tooling that pygame's build relies on. The commands above ensure that `setuptools` is available.

If the build still fails, use a stable Python release with official pygame wheels (for example Python 3.12) instead of preview versions such as Python 3.14, or install pygame from the prebuilt wheels available on [pip](https://pypi.org/project/pygame/).

Once pygame is installed, run the game with:

```bash
python pyramid.py
```
