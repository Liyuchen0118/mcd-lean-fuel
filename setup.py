"""setup.py 兜底，兼容旧版 setuptools（如 pip 21.x）的 editable 安装。

新版本（setuptools >= 64）会自动读 pyproject.toml；旧版本通过此文件兜底。
"""
from setuptools import setup, find_packages

setup(
    name="mcleanfuel",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[],
    extras_require={"dev": ["pytest>=7.0", "pytest-cov>=4.0"]},
    entry_points={
        "console_scripts": [
            "mcleanfuel = mcleanfuel.cli:main",
            "mcleanfuel-plan = mcleanfuel.cli:plan_cmd",
        ],
    },
    python_requires=">=3.9",
)
