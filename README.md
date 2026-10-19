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

## YOLO Validation am laufenden Band

1. Kamera und Förderband verbinden und in der Bandkarte **Rechts** als
   Vorwärtsrichtung bestätigen. Ein Bandnullpunkt ist für die Validation nicht nötig.
2. Rechts oben ein OBB-Modell auswählen und **Laden** wählen. Im Reiter
   **YOLO Validation** erscheinen alle Modellposen mit ihren Zählern, auch ohne Treffer.
3. Die Bandgeschwindigkeit einstellen (Standard 10 mm/s). Die Bildbewegung ist
   unabhängig von der Bandrichtung **rechts → links**.
4. Die rote Abschlusslinie im Livebild anklicken/ziehen oder als Prozentwert einstellen.
   Standard sind 5 % vom linken Bildrand; die blaue Linie markiert den Eintrittsbereich.
5. **Validation starten** startet nach erfolgreicher Kamerainferenz beide Bandmotoren.
   Räumlich getrennte Bauteile rechts ins Bild einlaufen lassen. Bereits mitten im
   Bild liegende Teile werden nicht als neue Durchfahrt übernommen.
6. Aktuelle Track-IDs und Posenschätzungen sowie abgeschlossene Bauteile und
   Summen beobachten. Eine geänderte Geschwindigkeit gilt nach **Übernehmen**.
7. **Stop** beendet Validation und Band gemeinsam. Der globale Stop bleibt auch beim
   Wechsel zum Aufnahme-Reiter sichtbar. Automatische Aufnahme und Validation
   können nicht gleichzeitig laufen.

Jedes Bauteil erhält eine eigene Track-ID. Seine Pose wird über mehrere vollständig
sichtbare Beobachtungen nach ihrer Konfidenz gewichtet abgestimmt: standardmäßig mindestens
fünf Beobachtungen über 0,25 s und mindestens 70 % Konsens. Die Werte stehen unter
**Erweiterte Auswertung**. Ein Klassenwechsel erzeugt keinen neuen Track.
Der Konsensanteil beschreibt die Übereinstimmung der Beobachtungen und ist keine
kalibrierte Wahrscheinlichkeit für eine richtige Pose.

Sobald die **linkeste OBB-Ecke** die Abschlusslinie erreicht, friert das Ergebnis ein;
die überquerende und alle späteren Beobachtungen beeinflussen die Pose nicht mehr.
Mehrdeutige Zuordnungen liefern keinen sicheren Posezähler. Nach mehr als 0,5 s
ohne passende Erkennung wird jeder begonnene Track als **Unvollständig** geführt;
ein wieder auftauchender Track bleibt kurzzeitig gegen erneute Zählung gesperrt.
Zu wenige oder widersprüchliche Beobachtungen ergeben **Unklar**.
Auch ein nur einmal erkanntes Teil und ein Teil ohne gültige, vollständig sichtbare
Beobachtung erscheinen in der Liste. Einzel-Erkennungen werden nicht als mögliche
Fehldetektionen verworfen. Die Spalte **Grund** erklärt den Abschluss; die Übersicht
zeigt die Gesamtzahl abgeschlossener Tracks einschließlich aller Fehlversuche.
Diese Fälle sind auch in Sicherung und Export enthalten und erhöhen keinen Posezähler.
Teile, die YOLO während der gesamten Durchfahrt nie erkennt, benötigen zur Erfassung
eine unabhängige Anwesenheits- oder Referenzzählung.

Sitzungen werden automatisch unter dem angezeigten Sicherungsordner gespeichert,
standardmäßig `Pictures\YOLOValidation\validation_…`. **Exportieren …** erzeugt
einen neuen Unterordner mit `bauteile.csv`, `summen.csv`, `session.json` und
`bilder/`. CSV verwendet UTF-8 mit BOM und Semikolon. Pro Bauteil wird ein
vollständig sichtbares Belegbild gesichert; wenn keine solche Beobachtung vorliegt,
bleibt der Bildverweis leer. Ein Doppelklick auf einen abgeschlossenen Eintrag
öffnet den Beleg. Laufende Exporte enthalten nur bereits abgeschlossene Bauteile.

**Liste zurücksetzen** ist bei gestoppter Validation möglich und lässt vorhandene
Sicherungssitzungen und Exporte bestehen. Ein Modellwechsel beginnt nach bestätigtem
Zurücksetzen eine neue Sitzung. Die erste Version liefert Posehäufigkeiten pro
Durchfahrt; für eine Genauigkeitsmessung werden zusätzlich bekannte Sollposen benötigt.

## Bilder weiterverwenden

- Für automatische Labels: Bauteilserien und eine Leerbildserie mit identischen Aufnahmeparametern aufnehmen.
- **OBB-Labels …:** Serien zuordnen, Anker und Bildauswahl prüfen, dann den Datensatz exportieren.
- **YOLO-Training …:** Datensatz laden, Bilder prüfen und **Training starten** wählen.
- **Modell …** und **YOLO Live-Erkennung:** Trainiertes OBB-Modell im Kamerabild verwenden.
- **Speicher bereinigen …:** Speicherbedarf analysieren und Bereinigung gezielt bestätigen.

## Hilfe

- **Keine Kamera:** IP/Netzwerkkarte und CTI-Pfad prüfen; andere Kamera-Clients schließen.
- **Kein Licht:** Bluetooth am Panel aktivieren und die Smartphone-App trennen.
- **Licht gefunden, aber „Unreachable“:** Unter Windows → Bluetooth und Geräte die gespeicherten
  Neewer-Kopplungen entfernen, die Panels neu starten und direkt in der GUI verbinden.
  Die Panels müssen für diese Anwendung nicht in den Windows-Einstellungen gekoppelt werden.
- **Kein Band:** ADS-Route und SPS-Verbindung prüfen (`192.168.10.23`, AMS `10.145.4.14.1.1`, Port `851`).
- Logs: `%LOCALAPPDATA%\AutomatedImageCapture\logs\dashboard.log`.
- **Förderband-Positionsrückmeldung noch ungeprüft:** In der Bandkarte einen kurzen
  manuellen Fahrweg wählen und **Links** oder **Rechts** betätigen. Dabei muss sich
  **SPS-Position** ändern. Anschließend das Bauteil wieder hinten platzieren und
  **Aktuelle Position = 0 mm** wählen. Die Prüfung benötigt eine beobachtete
  Encoderbewegung seit dem GUI-Start; das Setzen des Nullpunkts allein genügt nicht.
- [Technische Referenz](docs/technical-reference.md): Bildrate, Aufnahmeformate, Labeling, Training und Tests.
- [Projektübergabe](PROJECT_HANDOFF.md): Hardware, SPS und PC-Migration.
- [Windows-Verknüpfungen](WindowsLaunchers/README.md): Installation und Optionen.
