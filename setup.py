"""Compatibility shim for the system Python 3.9 / pip 21 bundled on macOS."""

from setuptools import find_packages, setup


setup(
    name="daily-ai-radar",
    version="0.4.0",
    description="Daily AI news and MLLM/VLA autonomous-driving paper radar",
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=True,
    package_data={
        "daily_radar": [
            "web/templates/*.html",
            "web/static/*.css",
            "web/static/*.js",
        ]
    },
    python_requires=">=3.9",
    install_requires=[
        "fastapi>=0.109,<0.116",
        "jinja2>=3.1,<4",
        "PyYAML>=6,<7",
        "uvicorn>=0.23,<0.35",
    ],
    entry_points={"console_scripts": ["daily-radar=daily_radar.cli:main"]},
)
