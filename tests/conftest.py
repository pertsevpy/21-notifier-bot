"""автоматически настраивать pytest"""

import sys
import os

# Добавляем корень проекта в sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
