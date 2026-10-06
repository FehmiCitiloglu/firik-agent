"""firik-agent masaüstü uygulaması doğrudan çalıştırma giriş noktası."""
from __future__ import annotations

import sys
from firik_agent.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
