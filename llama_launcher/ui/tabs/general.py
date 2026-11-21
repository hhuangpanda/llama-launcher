"""General tab: port, listen address, API key, startup."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox

from llama_launcher import paths
from llama_launcher.launch import listen_hosts
from llama_launcher.ui.tabs.base import SettingsTab
from llama_launcher.ui.widgets import open_path, tailscale_ipv4


class GeneralTab(SettingsTab):
    title = "General"

    def __init__(self, owner):
        super().__init__(owner)
        f = QFormLayout(self)
        self.port = QSpinBox(minimum=1, maximum=65535)
        self.listen = QComboBox(editable=True)
        self.listen_hint = QLabel()
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText("empty = no key")
        self.startup = QCheckBox("Launch at Windows sign-in")
        f.addRow("Port", self.port)
        f.addRow("Listen address", self.listen)
        f.addRow("", self.listen_hint)
        f.addRow("API key", self.api_key)
        f.addRow("", self.startup)
        row = QHBoxLayout()
        config_btn = QPushButton("Open config folder")
        config_btn.clicked.connect(lambda: open_path(paths.home()))
        logs_btn = QPushButton("Open logs folder")
        logs_btn.clicked.connect(lambda: open_path(paths.logs_dir()))
        row.addWidget(config_btn)
        row.addWidget(logs_btn)
        row.addStretch()
        f.addRow("", row)
        self.port.valueChanged.connect(lambda v: self._set("port", v))
        self.listen.currentTextChanged.connect(self._listen_changed)
        self.api_key.textChanged.connect(lambda v: self._set("api_key", v.strip()))
        self.startup.toggled.connect(lambda v: self._set("launch_at_startup", v))

    def _set(self, attr: str, value) -> None:
        self.set_field(self.owner.settings, attr, value)

    def _listen_changed(self, text: str) -> None:
        value = text.replace(" ", "")
        ts = set(tailscale_ipv4())
        hosts = set(listen_hosts(value))
        if "0.0.0.0" in hosts:
            hint = "All networks: this PC, LAN" + (" and Tailscale" if ts else "")
        elif hosts == {"127.0.0.1"}:
            hint = "This PC only"
        else:
            names = ["this PC" if h == "127.0.0.1" else f"Tailscale ({h})" if h in ts else h for h in sorted(hosts)]
            hint = "Only: " + ", ".join(names)
        if not ts:
            hint += "   (Tailscale not connected: its address isn't offered)"
        self.listen_hint.setText(hint)
        self._set("listen", value)

    def load(self) -> None:
        s = self.owner.settings
        self.port.setValue(s.port)
        self.listen.blockSignals(True)
        self.listen.clear()
        self.listen.addItems(["127.0.0.1", "0.0.0.0"])
        for ip in tailscale_ipv4():  # re-read on every open: Tailscale may have connected since
            self.listen.addItems([ip, f"127.0.0.1,{ip}"])
        self.listen.blockSignals(False)
        self.listen.setCurrentText(s.listen)
        self._listen_changed(self.listen.currentText())
        self.api_key.setText(s.api_key)
        self.startup.setChecked(s.launch_at_startup)
