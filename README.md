# BiBaZu_Automated_Image_Capture

- Automatisierte Trainingsbilder mit Baumer-Kamera, zwei Neewer-Lichtpanels und Zweimotoren-Förderband.
- Feste Rutsche; kein Roboter erforderlich.
- Optional: OBB-Labels erstellen, YOLO trainieren und Live-Erkennung nutzen.

## Voraussetzungen

- Windows 10/11 x64 und [uv](https://docs.astral.sh/uv/).
- Baumer Camera Explorer mit GenTL-Producer: `C:\Program Files\Baumer Camera Explorer\bgapi2_gige.cti`.
- Kamera im Netz `169.254.0.0/16`; aktuelle Kamera-IP: `169.254.117.70`.
- Bluetooth LE und zwei eingeschaltete Neewer RGB660 Pro II.
- Für Bandaufnahmen: TwinCAT ADS mit beidseitiger AMS-Route und passendem SPS-Stand (`MAIN.ConveyorCaptureInterfaceVersion = 1`); siehe [Projektübergabe](PROJECT_HANDOFF.md).
- Camera Explorer schließen, Neewer-Smartphone-App trennen und andere Bandsteuerungen beenden.

## Installation und Start

- PowerShell im Repository-Ordner öffnen und ausführen:

```powershell
uv sync
uv run python -m automated_image_capture
```

- Für spätere Starts: `uv run yolo-capture-dashboard`.
- Für eine Desktop-/Startmenü-Verknüpfung: `WindowsLaunchers/Verknuepfungen-installieren.cmd` doppelklicken; danach **BiBaZu Automated Image Capture** starten.
- Unter **Einstellungen …** Kamera-IP, CTI-Pfad und Geräteverbindungen prüfen.

## Bilder aufnehmen

1. Kamera, SPS und beide Panels einschalten; **Alle verbinden** wählen.
2. Livebild prüfen und die Belichtung einstellen.
3. Für Bandserien: **Links**/**Rechts** kurz testen, Positionsrückmeldung prüfen und **Vorwärtsrichtung** festlegen.
4. Bauteil hinten platzieren und **Aktuelle Position = 0 mm** wählen.
5. **Aufnahme konfigurieren …** öffnen: Ausgabeordner, Lichtvariation, optional Belichtungsvariation und Bandfahrt einstellen.
6. **Aufnahme starten** wählen und den freien Arbeitsraum bestätigen.

### Aufnahmemodus wählen

- **Diskrete Stationen:** Das Band hält an jeder Position für die Licht- und Belichtungsreihe.
- **Kontinuierliche synchronisierte Fahrt:** Bilder während der Hin- und Rückfahrt aufnehmen.
- Ohne Bandfahrt: Licht- und Belichtungsreihe an der festen Position aufnehmen.
- **Exaktes Raster:** Feste Helligkeitskombinationen abarbeiten.
- **Schnelle Rampe:** Bilder bei laufendem Helligkeitswechsel aufnehmen; die Kamera muss die gewählte Bildrate erreichen.

### Ergebnisse und Fortsetzen

- Jede Serie liegt im Ausgabeordner unter `capture_YYYYMMDD_HHMMSS`.
- Jedes Bild wird als PNG mit gleichnamiger YAML-Datei für die Aufnahmeparameter gespeichert.
- **Aufnahme stoppen** beendet die Aufnahme und stoppt das Förderband.
- **Aufnahme fortsetzen** lädt eine unterbrochene Sitzung über `capture_session.json`, auch nach einem App-Neustart.

## Bilder weiterverwenden

- Für automatische Labels: Bauteilserien und eine Leerbildserie mit identischen Aufnahmeparametern aufnehmen.
- **OBB-Labels …:** Serien zuordnen, Anker und Bildauswahl prüfen, dann den Datensatz exportieren.
- **YOLO-Training …:** Datensatz laden, Bilder prüfen und **Training starten** wählen.
- **Modell …** und **YOLO Live-Erkennung:** Trainiertes OBB-Modell im Kamerabild verwenden.
- **Speicher bereinigen …:** Speicherbedarf analysieren und Bereinigung gezielt bestätigen.

## Hilfe

- **Keine Kamera:** IP/Netzwerkkarte und CTI-Pfad prüfen; andere Kamera-Clients schließen.
- **Kein Licht:** Bluetooth am Panel aktivieren und die Smartphone-App trennen.
- **Kein Band:** ADS-Route und SPS-Verbindung prüfen (`192.168.10.23`, AMS `10.145.4.14.1.1`, Port `851`).
- Logs: `%LOCALAPPDATA%\AutomatedImageCapture\logs\dashboard.log`.
- [Technische Referenz](docs/technical-reference.md): Bildrate, Aufnahmeformate, Labeling, Training und Tests.
- [Projektübergabe](PROJECT_HANDOFF.md): Hardware, SPS und PC-Migration.
- [Windows-Verknüpfungen](WindowsLaunchers/README.md): Installation und Optionen.
