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

In der Förderbandkarte legt **Vorwärtsrichtung** fest, ob die Hinfahrt der
automatischen Aufnahme nach **Links** oder **Rechts** führt. Die Aufnahmeübersicht
zeigt Hinfahrt und entgegengesetzte Rückfahrt sofort an; die Auswahl wird gespeichert.
Während einer Aufnahme oder vorbereiteten/laufenden Fahrt ist die Änderung gesperrt.
Die Links-/Rechts-Tasten bleiben manuelle Testfahrten; die Richtungsauswahl allein
startet keine Bewegung.

**Aktuelle Position = 0 mm** setzt nur den lokalen Bezugspunkt und bewegt das Band
nicht. Die Taste ist bei lesbarer Position, Stillstand und ohne aktuellen Antriebsfehler
verfügbar, auch im Standby. Ein alter SPS-Status 5 kann nach einem behobenen Fehler
stehen bleiben. Bei der aktuellen Tandem-SPS sind dann manuelle Testfahrten wieder
möglich, sofern Fehlerflag und Servo-Fehlercode null sind; die normale Antriebsvorbereitung
und SPS-Prüfung laufen weiterhin vor jedem Fahrbefehl. Für synchronisierte Aufnahmen
zuerst die Encoderänderung durch eine kurze Testfahrt prüfen und danach den Nullpunkt setzen.
Beim Verbinden nach App-Start oder ADS-Neuverbindung wird ein alter Status 5 einmalig
quittiert, wenn beide Motoren inaktiv sind, alle aktuellen Fehlersignale frei sind und
eine zweite Positionsabfrage Stillstand bestätigt. Dabei wird nur der historische
Diagnosewert gelöscht; Motorfreigabe, Antriebsreset und Fahrbefehle bleiben unangetastet.
Aktuelle Fehler, laufende Aufträge und andere Statuscodes werden nicht automatisch quittiert.

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
4. Die rote Abschlusslinie und die blaue Eintrittslinie im Livebild anklicken/ziehen
   oder als Prozentwerte einstellen. Beim Ziehen wird die nächstgelegene Linie gewählt.
   Standard sind 5 % und 75 % vom linken Bildrand. Die Abschlusslinie darf auch über
   50 % liegen, bleibt aber immer links von der Eintrittslinie. Beide Linien lassen
   sich vor dem Start einstellen und werden mit den Sitzungseinstellungen gespeichert.
5. **Validation starten** startet nach erfolgreicher Kamerainferenz beide Bandmotoren.
   Räumlich getrennte Bauteile rechts ins Bild einlaufen lassen. Bereits mitten im
   Bild liegende Teile werden nicht als neue Durchfahrt übernommen.
   Die Auswertung eines Bauteils beginnt erst, wenn seine **rechteste OBB-Ecke**
   die blaue Eintrittslinie von rechts erreicht oder überschreitet. Frühere
   Erkennungen dienen nur der Bewegungsverfolgung; sie liefern keine Stimmen,
   Erstbilder oder abgeschlossenen Ergebnisse, auch nicht bei Erkennungsverlust.
6. Aktuelle Track-IDs und Posenschätzungen sowie abgeschlossene Bauteile und
   Summen beobachten. Eine geänderte Geschwindigkeit gilt nach **Übernehmen**.
7. **Stop** beendet Validation und Band gemeinsam. Der globale Stop bleibt auch beim
   Wechsel zum Aufnahme-Reiter sichtbar. Automatische Aufnahme und Validation
   können nicht gleichzeitig laufen.

Jedes Bauteil erhält eine eigene Track-ID. Jede gültige Erkennung rechts der
Abschlusslinie zählt einmal. OBBs dürfen den oberen oder unteren Kamerabildrand
berühren oder überschreiten, solange sie vertikal noch ins Bild reichen. Der rechte
Bildrand darf weiterhin nicht berührt oder überschritten werden. Der Erkennungsanteil
ist **Anzahl der häufigsten Pose /
Anzahl aller gültigen Erkennungen**. Bei **mehr als 50 %** wird diese Pose als
**Erkannt** übernommen und ihr Bauteilzähler erhöht: 51/100 genügt, genau 50/100 nicht.
Die Konfidenzen gewichten die Stimmen nicht; es gibt keine zusätzliche Mindestzahl
oder Mindestdauer. Auch 1/1 ergibt 100 %. **Stimmen / Gesamt** und **Anteil** zeigen
die Grundlage der Entscheidung. Unter **Erweiterte Auswertung** ist die Schwelle
anpassbar. Beim Umstieg von der gewichteten Auswertung oder der bisherigen
90-%-Voreinstellung wird die Schwelle einmalig auf 50 % gesetzt.
Ein Klassenwechsel erzeugt keinen neuen Track. Der Erkennungsanteil beschreibt die
Übereinstimmung der Beobachtungen und ist keine kalibrierte Modellwahrscheinlichkeit.

Sobald die **linkeste OBB-Ecke** die Abschlusslinie erreicht, friert das Ergebnis ein;
die überquerende und alle späteren Beobachtungen beeinflussen die Pose nicht mehr.
Mehrdeutige Zuordnungen zwischen Bauteilen liefern keinen bestätigten Posezähler.
Nach mehr als 0,5 s ohne passende Erkennung wird der Track abgeschlossen und die
Pose aus seinen bisherigen Stimmen entschieden. Ein Verlust vor der Abschlusslinie
verhindert eine klare Pose nicht mehr. Dasselbe gilt bei Stop oder Geräteausfall.
Ohne ausreichenden Erkennungsanteil lautet die Pose **Unklar**; ohne gültige Stimmen
bei abgebrochener Verfolgung **Unvollständig**. Ein wieder auftauchender abgeschlossener
Track bleibt kurzzeitig gegen erneute Zählung gesperrt und erhält keine neuen Stimmen.
Auch ein nur einmal erkanntes Teil und ein Teil ohne gültige
Beobachtung erscheinen in der Liste. Einzel-Erkennungen werden nicht als mögliche
Fehldetektionen verworfen. Die Spalte **Grund** erklärt den Abschluss; die Übersicht
zeigt die Gesamtzahl abgeschlossener Tracks einschließlich aller Fehlversuche.
Alle Fälle sind in Sicherung und Export enthalten. Nur **Erkannt** erhöht einen Posezähler.
Für jeden an der Eintrittslinie begonnenen Track wird sofort das vollständige
erste Auswertungsbild mit OBB,
Pose und Konfidenz unter `erstbilder/` gespeichert, auch bei Verdeckung, Randanschnitt
oder null gültigen Beobachtungen. Doppelklick auf **Erstbild** öffnet es; fehlt ein
gültiger Posebeleg, öffnet auch der normale Doppelklick dieses Bild. Die ungeprüfte
Ersterkennung bleibt getrennt vom Posekonsens. CSV/JSON enthalten `first_image`,
`first_class_id` und `first_confidence`; der Export enthält die Erstbilder ebenfalls.
CSV und JSON enthalten `observations`, `winning_observations`, `pose_observations`
und `consensus` für Gesamtzahl, Mehrheitszahl, Stimmen je Pose und Erkennungsanteil.
`pass_completed` hält getrennt fest, ob die Abschlusslinie beobachtet wurde;
`reason` dokumentiert etwa einen Trackingverlust. Auch bei `pass_completed=false`
kann eine klare Pose als **Erkannt** gezählt werden. Nicht bestätigte Kandidaten
bleiben als **vorläufig** unter `candidate_class_id`/`candidate_pose` nachvollziehbar.
Teile, die YOLO während der gesamten Durchfahrt nie erkennt, benötigen zur Erfassung
eine unabhängige Anwesenheits- oder Referenzzählung.

Sitzungen werden automatisch unter dem angezeigten Sicherungsordner gespeichert,
standardmäßig `Pictures\YOLOValidation\validation_…`. **Exportieren …** erzeugt
einen neuen Unterordner mit `bauteile.csv`, `summen.csv`, `session.json` und
`bilder/`. CSV verwendet UTF-8 mit BOM und Semikolon. Pro Bauteil wird ein
Belegbild einer gültigen Beobachtung gesichert, auch bei Randanschnitt oben oder unten;
wenn keine solche Beobachtung vorliegt,
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
- **Doppelte Posen filtern** unter den Live-YOLO-Reglern ist standardmäßig aktiviert.
  Bei überlappenden OBBs bleibt die Pose mit der höchsten Konfidenz; getrennte Teile
  bleiben sichtbar. **Überlappung (IoU)** bestimmt die Schwelle (Standard 0,60):
  kleinere Werte filtern stärker. Änderungen gelten nach Enter/Verlassen des Felds,
  der Schalter sofort; beide Einstellungen bleiben nach einem Neustart erhalten.
  Die Validation behält zusätzlich ihren eigenen Duplikatfilter bei IoU 0,60.
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
