from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .app_paths import default_paths


ENGINE_NAME = "gabbee"
ENGINE_LONGNAME = "Gabbee Voice Input"
COMPONENT_NAME = "org.freedesktop.IBus.Gabbee"
ENGINE_OBJECT_PATH = "/org/freedesktop/IBus/Gabbee"


def render_component_xml(executable: str) -> str:
    escaped_exec = xml_escape(f"{executable} --ibus")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<component type="inputmethod">
  <name>{COMPONENT_NAME}</name>
  <description>Gabbee voice input component</description>
  <exec>{escaped_exec}</exec>
  <version>0.1.0</version>
  <author>cabewse</author>
  <license>MIT</license>
  <homepage>https://local.gabbee</homepage>
  <textdomain>gabbee</textdomain>
  <engines>
    <engine>
      <name>{ENGINE_NAME}</name>
      <language>en</language>
      <license>MIT</license>
      <author>cabewse</author>
      <icon>gabbee</icon>
      <layout>default</layout>
      <symbol>GB</symbol>
      <longname>{ENGINE_LONGNAME}</longname>
      <description>English voice input through IBus</description>
      <rank>0</rank>
    </engine>
  </engines>
</component>
"""


def write_user_component_file(executable: str, destination: Path | None = None) -> Path:
    paths = default_paths()
    destination = destination or paths.ibus_component_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_component_xml(executable), encoding="utf-8")
    return destination
