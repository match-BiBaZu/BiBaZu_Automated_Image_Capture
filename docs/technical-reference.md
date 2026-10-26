# Technische Referenz

Schnellstart und Aufnahmeablauf: [README](../README.md).

Eine ausführliche fachliche und technische Projektübergabe einschließlich PC-Migration,
Hardwarevoraussetzungen, Datenformaten und offenen Punkten steht in
[`PROJECT_HANDOFF.md`](../PROJECT_HANDOFF.md).

PyQt6-Dashboard für automatisierte YOLO-Trainingsbilder am Zweimotoren-Förderband.
Die Rutsche ist fest auf einem Gestell montiert; ihre Neigung wird nicht verstellt.
Die Anwendung verbindet die Baumer-GigE-Kamera, das gemeinsame TwinCAT-Förderband
und zwei Neewer RGB660 Pro II über Bluetooth LE.

Livebild, getrennte Lichtsteuerung, Raster-/Rampenaufnahme, Belichtungsvariation,
PNG-/YAML-Speicherung, Fortsetzen, OBB-Annotation, Kuratierung, YOLO-Training,
Liveerkennung und Speicherbereinigung sind enthalten. Die Anwendung benötigt
keinen Roboter und kein Roboterprogramm.

## YOLO Validation: Datenfluss und Grenzen

`LiveInferenceConfig.duplicate_filter` aktiviert standardmäßig die klassenunabhängige
OBB-Unterdrückung vor Overlay und Ausgabe von `InferenceFrame.detections`.
`duplicate_iou` (Standard 0,60) verwendet die tatsächlichen gedrehten Polygone;
die jeweils stärkste Erkennung bleibt erhalten. Schalter und Schwelle stehen unter
den Live-YOLO-Reglern, werden in QSettings gespeichert und wirken ohne Modellneuladen.
Während der Validation sind sie gesperrt; die Inferenzkonfiguration wird in den
Sitzungsmetadaten mitgespeichert. Der Tracker nutzt denselben Filter zusätzlich mit
seiner bisherigen festen IoU-Schwelle 0,60, auch wenn der Livefilter ausgeschaltet ist.

Der Reiter verwendet dieselbe Modellauswahl und denselben Inferenzworker wie die
Liveerkennung. `InferenceModelInfo` meldet beim Laden Dateipfad, SHA-256, Modelltyp
und Klassenliste. `InferenceFrame.raw_image` liefert zusätzlich das unveränderte
RGB-Bild für Belege. Für die Validation wird der separate Aufnahmebildpfad
mit dem gewählten Inferenzlimit aktiviert; Vorschau-FPS begrenzen diesen Pfad nicht.
Ein direkter Consumer übergibt das bereits kopierte Kamerabild im Kamerathread an
den threadsicheren neuesten-Bild-Slot des Inferenzworkers. Dabei erfolgen weder
GUI-Aufrufe noch Farbkonvertierungen. Erst nach Auswahl des neuesten Bildes wandelt
der Inferenzworker Mono8 in drei Kanäle um; für RGB→BGR verwendet er OpenCV statt
einer langsamen NumPy-Kanalkopie. Der GUI-Ereignispfad kann so keine Warteschlange
alter Validierungsbilder erzeugen. Nach Stop wird der Consumer entfernt; die
automatische Aufnahme verwendet weiterhin das vollständige `capture_frame_ready`-Signal.
`InferenceFrame.timings` erfasst Bildzulauf, Farbkonvertierung, Modellaufruf,
Backend-Zeiten und Overlay. Während der Validation stehen zusätzlich Bildalter,
Tracking und letzte GUI-Verarbeitungszeit unter `performance` in `session.json`.
Das Dashboard-Log schreibt alle fünf Sekunden eine Zeile `Validation-Leistung`;
ein Tooltip am YOLO-Status zeigt wesentliche aktuelle Werte.
Der GenTL-Abruf verwendet kurze native Wartezeiten (1 ms) und `try_fetch`-Abschnitte
von höchstens 2 ms mit kooperativer Pause. Das vermeidet lange native Warteaufrufe,
die bei der verwendeten GenICam-Anbindung den GIL halten und dadurch den
Inferenzthread ausbremsen können (vgl. [Harvester #508](https://github.com/genicam/harvesters/issues/508)).
Die gesamte Abruffrist bleibt von der Belichtungszeit abhängig; leere kurze Polls
gelten nicht als Stream-Ausfall. Stop und die bisherige Ausfallbehandlung bleiben wirksam.
Der Inferenzlimiter berechnet und prüft seine Deadlines ausschließlich mit
`time.monotonic()`. `time.perf_counter()` dient nur zur Laufzeitmessung: Unter
Python 3.12/Windows können die Zeitgeber unterschiedliche Bezugspunkte haben.
Eine Vermischung erzeugte auf diesem Rechner etwa 0,19 s zusätzliche Wartezeit
pro Bild und begrenzte den Worker auch bei höherem FPS-Limit auf wenige FPS.

`ValidationTracker` verwendet klassenunabhängige OBB-Unterdrückung (IoU 0,6),
Positionsvorhersage aus gemessener Bildgeschwindigkeit und Hungarian-Zuordnung
über SciPy. Kosten kombinieren vorhergesagte OBB-Überdeckung und Mittelpunktabstand.
Abstand, vertikale Verschiebung und Flächenverhältnis begrenzen zulässige Zuordnungen.
Bei konkurrierenden Zuordnungen mit weniger als 0,12 Kostendifferenz werden
keine Klassenstimmen ausgetauscht; die betroffenen Tracks bleiben unsicher.
Neue Bauteile müssen den rechten Eintrittsbereich berühren (standardmäßig letzte
25 % der Breite). Eintritts- und Abschlusslinie sind im Livebild und als Prozentwerte
einstellbar; es gilt 0 ≤ Abschluss < Eintritt < 100 %. Während eines Laufs sind beide
gesperrt. Die gespeicherten Einstellungen und Sitzungsmetadaten enthalten beide Werte.
Eine Verschiebung im Bild wird in Pixeln pro Zeit gemessen, nicht aus einer
unbekannten Millimeter-pro-Pixel-Kalibrierung abgeleitet.

Nur vollständig sichtbare OBBs rechts der Abschlusslinie stimmen ab. Jede gültige
Erkennung zählt genau einmal; Gewinner und Anteil werden allein aus `counts`
berechnet. Die Konfidenzsummen dienen nur dem Konfidenzmittel und der Belegauswahl.
Die eindeutige Pose benötigt standardmäßig **mehr als 90 %** der gültigen Stimmen,
ohne zusätzliche Mindestdauer oder Mindestzahl. Genau 90 % erfüllt die Regel nicht.
Mehrdeutige Identitätszuordnungen bleiben von einer bestätigten Pose ausgeschlossen.
Die Abschlussbeobachtung selbst wird ausgeschlossen. Verlorene Tracks werden nach
0,5 s abgeschlossen, aus den bisherigen Stimmen bewertet und noch bis zu 1,5 s
länger als gesperrte Identität gehalten, um kurzfristiges Wiederauftauchen nicht
erneut zu zählen. Auch bei Stop/Geräteausfall bleiben ausreichende Mehrheiten gültig.
Die Aussetzerfrist wird nach der Zuordnung des aktuellen Bildes geprüft. Eine
geometrisch eindeutig zugeordnete OBB an der Abschlusslinie schließt den Track
deshalb regulär aus den vorherigen Stimmen ab, auch wenn das aktuelle Bild verspätet
eintrifft. Bereits abgeschlossene Tracks werden nicht nachträglich verändert.
Bei vorhandenen Stimmen unterhalb oder auf der Schwelle ist das Ergebnis `Unklar`;
ohne gültige Stimmen bei abgebrochener Verfolgung `Unvollständig`.
`winning_observations` und `pose_observations` sichern die Mehrheitszahl und die
Stimmen pro Klasse. `pass_completed` dokumentiert unabhängig von der Pose, ob die
Abschlusslinie beobachtet wurde; ein Trackingverlust mit klarer Mehrheit kann daher
`Erkannt` sein und einen Posezähler erhöhen. Der ursprüngliche Abschlussgrund bleibt erhalten.
`session.json` nennt die Regel unter `vote_policy` (`detection_count_v1`, Vergleich `>`).
Die QSettings-Migration ignoriert alte Mindestzahl/-dauer und setzt die zuvor gewichtete
Schwelle einmalig auf 90 %. Danach bleiben angepasste Zählschwellen gespeichert.
Jeder im Eintrittsbereich angelegte Track wird abgeschlossen, auch bei nur einem
Treffer oder ohne gültige Klassenstimme. Es gibt keinen stillen Mindesttrefferfilter.
`reason` benennt Einzel-Erkennung, fehlende gültige Beobachtungen, mehrdeutige
Zuordnung oder verfehlte Konsensanforderungen; die GUI zeigt diesen Grund und
die Gesamtzahl inklusive Fehlversuchen. Die CSV-/JSON-Sicherung enthält dieselben Fälle.
Die Zählung erfasst begonnene YOLO-Tracks; nie detektierte Teile sind ohne eine
unabhängige Anwesenheits- oder Referenzzählung nicht messbar.
Überlappende oder verdeckte Teile sind kein garantierter Betriebsfall dieser Version.

`ValidationController` koordiniert Modell-Warmup, frische Kamerainferenz, Bandfreigabe,
Tracking, Stop und Sicherung. Fehler bzw. ausbleibende Bild-/Bandrückmeldungen stoppen
den Lauf; es gibt keinen automatischen Wiederanlauf. Die kontinuierliche Bandsteuerung
verwendet vorhandene ADS-Symbole `GuiConveyorSpeedMmPerSec`, `GuiConveyorEnabled` und
den bestehenden gestoppten Tandem-Konfigurationsablauf. Als Dauerlaufbestätigung werden
SPS-Busy/Enable sowie nicht-null Sollgeschwindigkeiten beider Motoren verlangt.
Die Bestätigung belegt den gestarteten SPS-Fahrauftrag; die Hardwareabnahme muss
zusätzlich die tatsächliche Bewegung beider Antriebe prüfen. `GuiConveyorReverse`
wird nicht verändert; Dauerlauf entspricht dem bisherigen manuellen Rechts-Befehl.
Aufnahme- und Validation-Steuerung sind gegenseitig gesperrt.

`ValidationSessionStore` schreibt seriell im Hintergrund mit höchstens 16
ausstehenden Schreibaufträgen. Beim Anlegen jedes Tracks wird das vollständige
Erstbild unabhängig von der Gültigkeit als Posebeobachtung unter `erstbilder/`
gesichert. Es zeigt die ursprüngliche OBB/Klasse/Konfidenz mit dem Hinweis
„Erste Erkennung (ungeprüft)“ und wird durch spätere bessere Beobachtungen nicht
überschrieben. `first_image`, `first_class_id` und `first_confidence` stehen in
Ergebnis-JSON und Export-CSV; die Erstbilder werden mitexportiert. Diese Sicherung
liefert keine zusätzliche Poseabstimmung und verändert weder Status noch Zähler.
Je Track/Klasse wird weiterhin nur der bislang beste gültige
Beleg vorgehalten; bei Abschluss wird das Bild der gewählten Pose unter `bilder/`
fixiert. `part_*.json` hält einzelne Ergebnisse, `session.json` wird atomar ersetzt.
Ein Export wird hinter den zugehörigen Schreibaufträgen eingeordnet und verwendet
einen unveränderlichen Snapshot der abgeschlossenen Ergebnisse. Jeder Export
erhält einen neuen Ordner und überschreibt keine frühere Sicherung.
Die Oberfläche beginnt nach einem Neustart mit einer leeren Validierungsliste;
bisherige Sitzungen bleiben im Sicherungsordner erhalten.

Automatisierte Abnahme: `tests/test_validation.py`, die ergänzten Band-/Inferenztests
und GUI-Tests. Physische Abnahme: zunächst ein Teil, dann zwei getrennte Teile;
Bandrichtung, Track-IDs, Abschlusslinie, Posezähler und Belegbilder kontrollieren.

## Voraussetzungen

- Windows 10/11 x64
- [uv](https://docs.astral.sh/uv/)
- Baumer Camera Explorer beziehungsweise der Producer
  `C:\Program Files\Baumer Camera Explorer\bgapi2_gige.cti`
- Kamera-Netzwerk `169.254.0.0/16`, hier Kamera `169.254.117.70`
- Bluetooth-LE-Adapter und bis zu zwei eingeschaltete RGB660 Pro II

Der Baumer Camera Explorer muss geschlossen sein, da eine Kamera gewöhnlich nicht von zwei
GenTL-Clients gleichzeitig geöffnet werden kann. Die Neewer-Smartphone-App muss vom Panel
getrennt sein. Eine Windows-Bluetooth-Kopplung ist für den BLE-Zugriff normalerweise nicht nötig.

## Installation und Start

Windows shortcuts and their custom icon are owned by this repository:
after environment setup below, double-click
`WindowsLaunchers/Verknuepfungen-installieren.cmd`.
No BiBaZu_Big_Boi checkout is required.
See [Windows launcher options](../WindowsLaunchers/README.md).

```powershell
uv sync --extra dev
uv run python -m automated_image_capture
```

Alternativ steht nach der Installation das Kommando zur Verfügung:

```powershell
uv run yolo-capture-dashboard
```

Beim ersten Start werden keine Geräte automatisch verbunden und keine Lichtwerte verändert.
IPs, CTI-Pfad und Wiederverbindung lassen sich über **Einstellungen** ändern. Die erfolgreiche
Kamera- und Lichtauswahl wird über `QSettings` im Windows-Benutzerprofil gespeichert.

## Bedienung und Zweimotoren-Förderband

1. Camera Explorer und Neewer-App schließen; Kamera, SPS und beide Panels einschalten.
2. **Alle verbinden** oder die einzelnen Gerätekarten verwenden.
3. Livebild und Kamera-/Rohabruf-/Vorschau-FPS prüfen. Belichtung bleibt manuell einstellbar.
4. In der Bandkarte kurze Testfahrten über **Links**/**Rechts** ausführen und die
   Vorwärtsrichtung festlegen. Die echte Encoder-Rückmeldung muss sich ändern.
5. Bauteil hinten platzieren und **Aktuelle Position = 0 mm** wählen.
6. Beleuchtung einstellen oder eine automatische Serie konfigurieren.

TwinCAT ADS verwendet SPS `192.168.10.23`, AMS-Net-ID `10.145.4.14.1.1` und
PLC-Port 851. Die ADS-Runtime und beidseitige AMS-Route müssen eingerichtet sein.
`PressureControlGUI` darf währenddessen nicht dasselbe Band steuern.

Das vorhandene SPS-Projekt liegt in
`BiBaZu_Big_Boi/CSVSaver/TwinCAT Projekt3 - Kopie/TwinCAT Projekt3.sln`.
Motor 1 liegt an Device 3/Term 7, Motor 2 an Device 4/Term 10 (EL7201-0010).
Beide verwenden CSV-Modus 9, dieselbe Fahrkurve, Richtung -1, Verhältnis 1:1,
32:1-Getriebe und 50-mm-Rollen. Die nominelle Umrechnung beträgt
`pi * 50 / (200 * 32) = 0,0245436926 mm` pro virtuellem Vollschritt.
Der Adapter verwendet weiterhin die kompatiblen `MAIN.Stepper*`-Statusnamen;
deren Position stammt jetzt von den Servo-Encodern.

**Für diesen Umbau muss der aktualisierte SPS-Quellstand kompiliert und geladen werden.**
`MAIN.ConveyorCaptureInterfaceVersion = 1` kennzeichnet die angepassten endlichen
Aufnahmefahrten: maximal 250000 virtuelle Vollschritte und der gemeinsame
Geschwindigkeitsbereich bis 8148,733086 Schritte/s bzw. 200 mm/s. Ältere SPS-Stände
begrenzen diese Fahrten noch auf 500 Schritte/s; die GUI akzeptiert sie nicht.
Ein GUI-Start lädt oder aktiviert keine SPS-Konfiguration.

Erst ein angeforderter Fahrauftrag führt den bekannten gestoppten Konfigurationsablauf
aus: Stop beider Antriebe prüfen, MotorCount=2 und die gemeinsamen Einstellungen
setzen, gestoppt zurücksetzen und den Fahrmodus aktivieren. Die
Motorbeschleunigung ist im Aufnahmedialog einstellbar (Standard 50 rpm/s).
Verbindungsaufbau allein löst keine Fahrt aus. Stop während der Vorbereitung
verhindert den anschließenden Start.

Die SPS fährt ein Geschwindigkeitsprofil ohne Positionskorrektur. Die Anwendung
akzeptiert eine Restabweichung bis 1 mm (mindestens die bisherige
Vollschritt-Toleranz) und speichert die tatsächliche Position. Größere Abweichungen
führen nach der Rückmeldefrist zum Abbruch, ohne automatische Nachstellfahrt.
Die nächste Station wird aus der gemessenen Position und dem unveränderten
Nullpunkt berechnet. Vor dem Fortsetzen wird die Position erneut geprüft;
eine nötige Korrekturfahrt bleibt eine getrennt bestätigte Bedienaktion.

## Kamera-Bildrate

Der Kamera-Worker leert den GenTL-Datenstrom stets mit der von Belichtung, Kamera und Netzwerk
erreichbaren Rohbildrate. Unter **Einstellungen** kann das kameraseitige
`AcquisitionFrameRate`-Limit abgeschaltet werden; dies ist standardmäßig aktiv. Die dort separat
konfigurierte **Maximale Vorschau-FPS** begrenzt nur die Darstellung in der GUI. Während einer
automatischen Aufnahme erhält der Aufnahmecontroller einen eigenen Framepfad mit der im
Aufnahmedialog gewählten Bildrate. Mono-Bilder bleiben dort einkanalig und werden nicht unnötig
zu RGB verdreifacht. Die
YOLO-Liveerkennung besitzt weiterhin ihr eigenes FPS-Limit.

Die Belichtungszeit begrenzt die physikalisch mögliche Bildrate unmittelbar: 250000 µs erlauben
höchstens etwa 4 FPS, 5000 µs belichtungsseitig bis zu 200 FPS. Die tatsächlich gemessenen Werte
zeigt die Kamerakarte als **Kamera / Rohabruf / Vorschau**. Die schnelle Rampenaufnahme kann mit
bis zu 240 Bildern/s konfiguriert werden, startet aber nur, wenn der gemessene Rohabruf die
gewählte Rate erreicht. Bei zeitgesteuerten Hochgeschwindigkeitsaufnahmen werden weiterhin
verlustfreie, aber unkomprimierte PNGs geschrieben. Damit wird CPU-Kompression vermieden; der
Datenträger muss bei 1920 × 1200 Mono8 ungefähr 2,3 MB pro Bild aufnehmen können. Die
Writer-Warteschlange bricht kontrolliert ab, statt Arbeitsspeicher unbegrenzt zu belegen.

Für eine isolierte Messung bei geschlossener GUI:

```powershell
uv run python scripts/benchmark_camera.py --frames 300
```

## YOLO im Kamera-Livebild

Oberhalb des Kamerabilds lässt sich **YOLO Live-Erkennung** zuschalten. Beim ersten Start
wählt die GUI automatisch das neueste `best.pt` unter
`Pictures\Kk1_pose12_yolo26_obb\runs`; über **Modell …** kann jederzeit ein anderes
Ultralytics-OBB-Modell gewählt werden. Konfidenzschwelle und maximale Inferenz-Bildrate sind
direkt einstellbar und werden gespeichert.

Die erkannten orientierten Bounding Boxes, Klassen (`Pose 1`/`Pose 2`) und Konfidenzen werden
in das Livebild eingezeichnet. Neben den Reglern stehen Objektzahl und Laufzeit des letzten
Frames. Die Inferenz läuft auf der GPU in einem eigenen Thread und verarbeitet nur das jeweils
neueste Kamerabild. Dadurch stauen sich bei hoher Kamera-Bildrate keine alten Bilder an und die
Hardware-Bedienung bleibt responsiv. Der erste Frame benötigt wegen der CUDA-Initialisierung
deutlich länger als die folgenden Frames.

## Automatische Aufnahme

1. Kamera und beide Panels verbinden; für Bandserien zusätzlich das Förderband.
2. Unter **Aufnahme konfigurieren** Speicherort, Lichtmodus und optional
   Belichtungsvariation einstellen. Die Rutsche hat eine feste Ansicht.
3. Für Bandserien Vorwärtsrichtung und Nullpunkt festlegen. Synchronisierte
   Serien benötigen eine durch Testfahrt geprüfte Positionsrückmeldung.
4. **Aufnahme starten** wählen und die Freigabe des Arbeitsraums bestätigen.

**Diskrete Stationen:** Förderbandstation → Belichtung → vollständige Lichtvariation.
Standardweg: `0, 10, 20, 30, 40, 50, 40, 30, 20, 10, 0 mm`.
Hin- und Rückweg werden getrennt gespeichert. Ohne Positionsvariation wird die
Licht-/Belichtungsreihe einmal an der festen Ansicht aufgenommen.

**Kontinuierliche synchronisierte Fahrt:** Pro Belichtung fährt das Band
`0 → Maximum → 0 mm`, während Kamera und Panels weiterlaufen. Weg,
Geschwindigkeit, Beschleunigungs- und Bremsrampen bestimmen Zeit und geplante Positionen.
Bei 50 mm, 10 mm/s und 50 rpm/s beträgt die reine Rundfahrzeit nominal 14,889 s;
bei 6 Bildern/s sind 90 Samples geplant. Vorbereitungs- und Umkehrpausen kommen hinzu.
Viele Raster-Lichtpaare reduzieren die effektive Geschwindigkeit. Die YAMLs
enthalten stets sowohl geplante als auch gemessene Bandposition und Zeitabweichung.

**Exaktes Raster** arbeitet feste Helligkeitsschritte ab. Panel 1 läuft je
Panel-2-Wert vollständig durch. **Schnelle Rampe** nimmt während zeitbasierter
Dreieckskurven beider Panels kontinuierlich Bilder auf. Ohne Bandfahrt bleibt
das Standardprofil bei 60 Bildern in 10 Sekunden und Panelperioden 2,4/10 s.
Bei synchroner Bandfahrt werden die Lichtperioden auf die Rundfahrzeit skaliert.
Pro Panel läuft höchstens ein BLE-Befehl; überholte Zwischenziele werden übersprungen.

Jede Sitzung erhält `capture_YYYYMMDD_HHMMSS`. Neue Dateinamen verwenden
`img_000001_fixed_…`; zu jedem verlustfreien PNG gehört eine gleichnamige YAML
mit Kamera-, Licht-, Sequenz- und gegebenenfalls Banddaten. `chute.mounting: fixed`
und `chute.inclination_adjustable: false` dokumentieren das Gestell. Ein
Neigungswert wird nicht erfunden. Die Metadaten-/Checkpoint-Version ist 3.

**Aufnahme stoppen** beendet neue Aufträge und sendet den gemeinsamen Band-Stop.
Bei Abschluss/Stop wird eine variierte Belichtung auf den Verbindungswert zurückgesetzt.
`capture_session.json` speichert den Fortschritt atomar. Fortsetzen funktioniert
auch nach GUI-Neustart; vollständige PNG-/YAML-Paare werden übersprungen,
unvollständige Paare werden nicht überschrieben.

Alte Bildserien mit `ur…`/`ura-…` bleiben für Labeling, Training und Bereinigung
lesbar. Alte unterbrochene UR-Aufnahmesitzungen werden ausdrücklich abgelehnt,
weil ihre Winkelreihen und Bildindizes nicht zum festen Gestell passen.
Gespeicherte UR-IP-/Winkel-Einstellungen werden ignoriert; Licht-, Kamera-,
Belichtungs-, Band- und Speicherort-Einstellungen bleiben erhalten.

## Automatische OBB-Labels

Bei synchronen Förderbandaufnahmen liest das Labeling zusätzlich
`conveyor.measured_logical_offset_mm` aus jeder Bild-YAML. Die OBB-Mittelpunkte werden pro
Kameraansicht als gerade Bildbahn über der gemessenen Bandposition modelliert. Ein robuster
Ankerfilter verwirft Schatten, Rutschenkanten sowie unplausibel große Boxen, bevor Größe und
Orientierung bestimmt werden. Fehlerhafte Einzelsegmentierungen werden durch die Bahn
stabilisiert und im `label_report.csv` weiterhin als `REVIEW` nachvollziehbar markiert.
Fehlen Positionsmetadaten, bleibt das bisherige Konsensverfahren unverändert aktiv.

Bei mehreren konkurrierenden Bahnhypothesen zählt nicht nur die Trefferzahl, sondern auch die
abgedeckte Förderbandstrecke. Falls der Bildfortschritt durch Abtastverzug oder Schlupf nicht
linear zur Millimeterposition ist, bleibt die Mittelpunktbahn räumlich gerade und nur der
Fortschritt auf dieser Geraden wird robust quadratisch modelliert. Lange, dünne Ausläufer einer
Differenzmaske (zum Beispiel eine angeschlossene Rutschenkante) werden nur bei auffällig
schlecht gefüllten Rohboxen entfernt; dabei müssen mindestens 70 % der Maskenfläche erhalten
bleiben. Kompakte Masken verwenden unverändert den bisherigen Pfad.

Nach der einmaligen Segmentierung pausiert die GUI vor dem Export. Sie zeigt für jede Klasse
und jeden Kameraansicht sechs über die gesamte Bandstrecke verteilte Beispiele: Grün bedeutet
`ANKER`, Orange bedeutet eine aus der Bahn berechnete Box. Erst **Anker übernehmen** schreibt
noch nicht sofort den YOLO-Datensatz: Anschließend bewertet eine zweite Stufe globale
Helligkeit, Clipping, Dynamikumfang und den lokalen Kontrast in der erwarteten OBB. Verdächtige
Bilder erscheinen als Thumbnail-Galerie. Eindeutig schwarze oder weiße Bilder sowie sehr
dunkle Bilder mit weniger als zehn Grauwerten lokalem Objektsignal sind zum Ausschluss
vorausgewählt; kontrastierte dunkle Grenzfälle bleiben eine bewusste Nutzerentscheidung. Erst
**Auswahl übernehmen** startet den Export; bei Abbruch bleibt der Ausgabeordner unangetastet.
Ausgeschlossene Bilder stehen mit Sichtbarkeitsscore und Begründung weiterhin im
`label_report.csv`, werden aber weder als Trainingsbild noch als Label geschrieben.

Für synchronisierte Bandserien enthält `review/` je Klasse und Kameraansicht ein kompaktes Blatt
mit sechs über die Strecke verteilten Bildern sowie eine Klassenübersicht. Dadurch werden bei
hochauflösenden Serien nicht mehr pro einzelner Bandposition redundante Vollmasken und
2×3-Blätter erzeugt. Klassische Rasterserien behalten ihre bisherigen Konsensmasken.

Bildpaarregistrierung, Differenzsegmentierung und Sichtbarkeitsbewertung laufen parallel, da
jedes Vorder-/Leerbildpaar unabhängig ist. Die Workerzahl wird automatisch auf die Hälfte der
logischen CPUs, höchstens jedoch zwölf, begrenzt. OBB-Bahnmodell, Nutzerentscheidungen und
Dateiexport bleiben geordnet und deterministisch. Auf dem 24-Thread-Testrechner sank die reine
Df1a-Segmentierung eines 121-Bilder-Winkels bei identischen Ergebnissen von etwa 46 auf 7
Sekunden. Mehr als zwölf Worker brachten nur noch wenig Zusatzdurchsatz bei deutlich höherem
Speicherbedarf.

Über **OBB-Labels …** lässt sich aus beliebig vielen Bauteilserien und einer parametrisch
identischen Leerbildserie ein gemeinsamer YOLO-OBB-Datensatz erzeugen. Die Quellenliste startet
mit `Pose 1`, `Pose 2` und `Leere Rutsche`; weitere Posen lassen sich hinzufügen oder wieder
entfernen. Jede Pose erhält anhand ihrer Listenposition eine eigene Klassen-ID und in jeder
Zeile kann der zugehörige Aufnahmeordner gewählt werden. Das Tool paart die Aufnahmen anhand
von Kameraansicht, beiden Panelhelligkeiten und Belichtung. Vor der Differenzbildung wird jedes
Leerbild subpixelgenau registriert, damit kleine Wiederholabweichungen auf strukturierten
Untergründen nicht als Objekt erscheinen.

Alle Beleuchtungsvarianten derselben Klasse und Kameraansicht stimmen über eine gemeinsame
Segmentierungsmaske und eine gemeinsame orientierte Bounding Box ab. Einzelbilder mit
schwacher Übereinstimmung werden in `label_report.csv` als `REVIEW` markiert. Unter `review/`
entstehen repräsentative Overlays und Konsensmasken für jede Klasse und Kameraansicht.

Der Ausgabeordner ist direkt als Ultralytics-YOLO-OBB-Datensatz aufgebaut:

- `images/train`, `images/val`
- `labels/train`, `labels/val` mit acht normierten OBB-Koordinaten
- `data.yaml`
- `label_summary.json` und `label_report.csv`

Frühere Serien behalten ihren Train-/Val-Split nach vollständigen Kameraansichten.
Die feste Ansicht wird zunächst gemeinsam exportiert; die anschließende Trainingsaufbereitung
teilt vollständige Lichtgruppen auf. Optional werden die Leerbilder genau einmal mit leeren Labeldateien
als negative Beispiele übernommen. Ist dieselbe Lichtlage für alle Positivklassen als
unsichtbar ausgeschlossen, wird auch das korrespondierende Leerbild übersprungen. So lernt
YOLO nicht fälschlich „sehr dunkel = leer“.
Auf demselben Laufwerk nutzt das Tool Hardlinks, sodass dabei keine zweite Kopie der großen
PNG-Dateien entsteht.

Für reproduzierbare, versionierte Läufe ohne GUI steht außerdem
`scripts/generate_obb_cli.py` bereit. Mit `--review-snapshot` bleiben die Anker- und
Sichtbarkeitsübersichten dauerhaft neben dem erzeugten Datensatz erhalten.

Die beiden BLE-Adressen werden getrennt gespeichert, sodass „Alle verbinden“ nicht beide
Adapter demselben Panel zuordnet. Die Lichtwerte sind als „bestätigter letzter Befehl“
gekennzeichnet. Das BLE-Protokoll liefert
nicht in jeder Firmware verlässliche physische Istwerte zurück. Beim Beenden wird das Panel
nicht automatisch ausgeschaltet oder umgestellt.

## Kuratierung und YOLO26-OBB-Training

Über **YOLO-Training …** wird ein gemeinsamer, vom OBB-Labeltool erzeugter Datensatzordner
geladen. Klassen und Namen werden aus `label_summary.json` übernommen, sodass neben `Pose 1`
und `Pose 2` auch weitere Klassen ohne zusätzliche Quellfelder unterstützt werden. Leerbilder
werden dabei nur einmal übernommen. Der Review zeigt
automatisch auffällige Aufnahmen zuerst; ein entferntes Häkchen schließt ausschließlich dieses
Bild aus. Die Entscheidung wird in `curation.json` gespeichert und verändert keine Quelldatei.

Der erzeugte, versionierte Datensatz besitzt getrennte Ordner für Train, Validation und Test.
Zusammengehörige Lichtgruppen bleiben im selben Split. `dataset_manifest.json`
dokumentiert Quelle, Klasse, Beleuchtung, Split und Review-Entscheidung jeder Aufnahme.

Nach der Datensatzprüfung startet **Training starten** `yolo26n-obb.pt` in einem separaten
Prozess. Standardmäßig werden 200 Epochen bei 640 Pixeln mit Early Stopping, Batch 16 und acht
Loader-Prozessen trainiert. Batch und Loader sind im Dialog anpassbar; bei einem CUDA-
Speicherfehler sollte zuerst die Batchgröße reduziert werden. Vor dem GPU-Lauf werden die Bilder
einmalig verlustfrei auf die Trainingsgröße verkleinert und bei späteren Läufen wiederverwendet. Das
Training verwendet keine Spiegelungen oder starken Rotationen, da diese die beiden physisch
unterschiedlichen Bauteilposen verfälschen könnten. `best.pt`, Validierungs-/Testmetriken,
Confusion-Matrizen und die Fehlalarmrate auf leeren Testbildern liegen anschließend im
angezeigten Ergebnisordner.

Bei festen Ansichten werden Train/Val/Test reproduzierbar nach vollständigen
Lichtpaaren aufgeteilt (ungefähr 70/15/15). Alle Belichtungen, Bandstationen,
Hin-/Rückframes, Klassen und Leerbilder desselben Lichtpaars bleiben zusammen.
Mindestens drei verschiedene Lichtpaare sind nötig. Die alte Aufteilung nach
kompletten Ansichten bleibt für frühere Serien erhalten.

Dieselben Schritte sind ohne GUI reproduzierbar:

```powershell
uv run python -m automated_image_capture.training prepare --source <OBB-DATENSATZORDNER>
uv run python -m automated_image_capture.training train --dataset <DATENSATZORDNER>
uv run python -m automated_image_capture.training diagnose
```

Der erste Modellaufruf lädt die vortrainierten Gewichte, sofern sie noch nicht lokal vorhanden
sind. Das Projekt verwendet PyTorch mit CUDA 11.8; Batch 16 und acht Loader wurden auf der
RTX 2000 Ada mit 8 GB VRAM getestet.

## Speicherbereinigung

**Speicher bereinigen …** analysiert einen ausgewählten Projektordner, ohne ihn zunächst zu
verändern. Die Anzeige unterscheidet die logische Summe aller Pfade von der tatsächlich
belegten Größe: Aufnahme-, OBB- und Trainingsbilder sind häufig bereits NTFS-Hardlinks und
belegen deshalb nicht mehrfach Speicher.

Die sichere Voreinstellung kodiert erkannte Projektbilder bei unveränderter Auflösung als
einkanaliges, verlustfreies PNG mit Kompressionsstufe 3. Optional stehen eine maximale
Kantenlänge sowie JPEG und WebP zur Verfügung. Verkleinerung und Qualitätskompression sind
irreversibel und werden im Dialog entsprechend hervorgehoben. Die Labeling- und
Trainingspipeline liest PNG, JPEG und WebP.

Automatisch gelöscht werden nur validierte `_imgsz…`-Trainingscaches und
Ultralytics-`*.cache`-Dateien. Exakte Duplikate werden pfaderhaltend zu Hardlinks; Roh-YAML,
OBB-Labels, Review-Ergebnisse und Modell-Checkpoints bleiben erhalten. Nach der Analyse ist
eine zweite ausdrückliche Bestätigung erforderlich. Der Lauf arbeitet mit temporären Dateien,
prüft die erzeugten Bilder und Datensätze erneut und schreibt einen `cleanup_report_*.json` in
den gewählten Ordner. Abgeschlossene und unvollständige Aufnahmesitzungen werden strikt
unterschieden; laufende oder unterbrochene Sitzungen werden nicht verändert.

## Hardwarebedienung

Die Anwendung steuert das Band ausschließlich über die vorhandenen gemeinsamen
ADS-Fahr-, Stop- und Konfigurationsbefehle. SPS-Prüfungen für Antriebe,
Kommunikation und Encoder bleiben aktiv. Softwaretests simulieren Geräte und
lösen keine realen Fahrten aus.

## Tests

```powershell
uv run pytest
uv run ruff check .
```

Hardwaretests sind standardmäßig deaktiviert:

```powershell
$env:RUN_HARDWARE_TESTS = "1"
uv run pytest -m hardware tests/hardware
```

Die automatischen Hardwaretests öffnen die Kamera und lesen 100 Bilder. Der Lichttest prüft nur Scan und Verbindung. Sichtbare Lichtbefehle werden
bewusst manuell über die GUI abgenommen, damit ein Testlauf die Beleuchtung nicht überraschend
verändert. Förderbandfahrten werden separat am Aufbau geprüft.

## Fehlerdiagnose

- **Keine Kamera:** CTI-Pfad, dedizierte Netzwerkkarte und Camera Explorer prüfen.
- **Kamera belegt:** Camera Explorer oder einen anderen GenTL-Client schließen.
- **Kurzer Kameraaussetzer:** Die Karte wechselt zunächst auf „Eingeschränkt“ und startet den
  GenTL-Datenstrom automatisch neu. Erst wenn zwei Wiederherstellungsversuche scheitern, wird
  die laufende Aufnahmeserie unterbrochen und kann anschließend fortgesetzt werden.
- **Kein Licht:** Bluetooth-Symbol am Panel aktivieren, Smartphone-App trennen und näher an den
  PC bringen.
- **Panel sichtbar, aber GATT/„Could not start notify … Unreachable“:** Gespeicherte
  Neewer-Kopplungen unter Windows → Bluetooth und Geräte entfernen, Panels neu starten und
  direkt in der GUI verbinden. Veraltete Windows-Kopplungen können trotz sichtbarer
  Werbung den GATT-Zugriff auf beide Panels verhindern. Eine erneute Windows-Kopplung
  ist für die Anwendung nicht nötig. Am 19.10.2026 wurden beide RGB660 PRO nach dem
  Entfernen der gespeicherten Kopplungen wieder mit erfolgreicher Statusabfrage verbunden.

Die GUI führt BLE-Suche und Verbindungsaufbau beider Panels nacheinander aus.
Verbindungsaufbau und Freigabe haben Zeitlimits; Abbrechen und Beenden warten auf
die Freigabe eines bereits geöffneten Clients. Beim Verbinden werden nur Statusabfragen
gesendet, keine Leistungs-, Helligkeits- oder Farbbefehle.

Ein passiver BLE-Scan kann bei Bedarf mit `uv run python scripts/probe_ble.py` ausgeführt werden.
Mit `--connect <Adresse>` liest das Skript zusätzlich nur die angebotenen GATT-Service-UUIDs.

Rotierende Logs liegen unter
`%LOCALAPPDATA%\AutomatedImageCapture\logs\dashboard.log`. Es werden keine Bilder protokolliert.
