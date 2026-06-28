# Predpoveď počasia — SHMÚ ALADIN 4.5 km

Lokálna stránka, ktorá zobrazuje predpoveď počasia pre slovenské mestá z dát
[SHMÚ Open Data (model ALADIN 4.5 km)](https://opendata.shmu.sk/meteorology/weather/nwp/aladin/sk/4.5km).

Malý Python server stiahne najnovší beh modelu (GRIB2), dekóduje bodovú predpoveď
pre 10 miest a servíruje ju cez `index.html`, ktorá sa **každú hodinu automaticky obnoví**.

---

## 🚀 DEPLOY — ako to spustiť na inom Windows počítači

Stačia **2 súbory** a Python. Žiadny build, žiadna databáza.

### Krok 1 — nainštaluj Python

Ak na cieľovom počítači Python ešte nie je:

1. Stiahni Python 3.11+ z <https://www.python.org/downloads/windows/>
   (alebo `winget install Python.Python.3.12`).
2. V inštalátore **zaškrtni „Add python.exe to PATH"**.
3. Over v novom termináli (PowerShell):
   ```powershell
   python --version
   ```

### Krok 2 — skopíruj projekt

Na nový počítač stačí prekopírovať **iba tieto súbory** (napr. do `C:\shmu_pocko\`):

```
forecast_server.py     ← server (povinné)
index.html             ← stránka (povinné)
README.md              ← tento súbor (nepovinné)
```

> ⚠️ **NEKOPÍRUJ** priečinok `.grib_cache\` — to sú len dočasne stiahnuté dáta,
> server si ich na novom počítači stiahne sám. Pokojne ho vymaž.

Prenos je jedno z: USB kľúč, sieťový zdieľaný priečinok, ZIP cez e-mail, alebo
`git clone` ak je projekt v repozitári.

### Krok 3 — nainštaluj závislosti

V PowerShell-i v priečinku projektu:

```powershell
cd C:\shmu_pocko
python -m pip install --upgrade pip
python -m pip install eccodes ecmwflibs findlibs numpy
```

Týmto sa stiahne aj binárna knižnica **ecCodes** (cez balík `ecmwflibs`) — nič
sa neinštaluje systémovo, všetko ide do Python balíkov používateľa. Funguje to
aj na Python 3.14 (viď poznámka nižšie).

### Krok 4 — spusti server

```powershell
python forecast_server.py
```

Prvé spustenie trvá **~20–40 s** (sťahovanie + dekódovanie GRIB súborov).
Potom otvor v prehliadači:

```
http://localhost:8765
```

Hotovo. Stránka beží lokálne a každú hodinu sa sama obnoví.

---

### ✅ Rýchly „copy-paste" deploy

Celé naraz v PowerShell-i (po skopírovaní `forecast_server.py` a `index.html`):

```powershell
cd C:\shmu_pocko
python -m pip install eccodes ecmwflibs findlibs numpy
python forecast_server.py
```

---

### 🖱️ Spúšťanie dvojklikom (voliteľné)

Vytvor súbor `start.bat` vedľa `forecast_server.py`:

```bat
@echo off
cd /d "%~dp0"
start "" http://localhost:8765
python forecast_server.py
pause
```

Dvojklik na `start.bat` spustí server **a** otvorí stránku v prehliadači.

---

### ⏰ Automatický štart pri zapnutí Windows (voliteľné)

Aby server bežal stále na pozadí:

**Možnosť A — priečinok Po spustení (Startup):**
1. Stlač `Win + R`, napíš `shell:startup`, Enter.
2. Skopíruj sem zástupcu na `start.bat`.

**Možnosť B — Plánovač úloh (Task Scheduler), beží aj bez prihlásenia:**
```powershell
schtasks /create /tn "SHMU pocasie" /tr "python C:\shmu_pocko\forecast_server.py" /sc onstart /ru SYSTEM
```

---

### 🌐 Prístup z iných zariadení v sieti (voliteľné)

Štandardne server počúva iba na `127.0.0.1` (len tento počítač). Ak chceš stránku
otvoriť z mobilu/iného PC v rovnakej sieti:

1. V `forecast_server.py` zmeň v `main()`:
   ```python
   srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
   ```
2. Povoľ port vo Windows Firewall:
   ```powershell
   netsh advfirewall firewall add rule name="SHMU pocasie" dir=in action=allow protocol=TCP localport=8765
   ```
3. Z iného zariadenia otvor `http://<IP-adresa-PC>:8765`
   (IP zistíš cez `ipconfig`).

---

## 🔧 Riešenie problémov

| Problém | Riešenie |
|---|---|
| `python` sa nenašiel | Python nie je v PATH — preinštaluj so zaškrtnutým „Add to PATH", otvor **nový** terminál. |
| `Cannot find the ecCodes library` | Chýba `ecmwflibs`: `python -m pip install ecmwflibs findlibs`. |
| `No module named 'ecmwflibs'` aj po inštalácii | `pip` a `python` sú **iné interpretery** (napr. Microsoft Store Python). Nainštaluj balíky tým istým `python`: `python -m pip install eccodes ecmwflibs findlibs numpy`, potom `python forecast_server.py`. Server pri chybe vypíše presnú cestu k interpreteru aj príkaz na skopírovanie. Over cez `where python`. |
| `UnicodeEncodeError` v konzole | Staršia verzia — server už nastavuje UTF-8 výstup; stiahni aktuálny `forecast_server.py`. |
| Port 8765 je obsadený | Zmeň `PORT` na vrchu `forecast_server.py` (napr. 8080). |
| Stránka ukazuje „chyba spojenia" | Beží `forecast_server.py`? Skontroluj okno terminálu. |
| Prvé načítanie je pomalé | Normálne (~20–40 s) — sťahuje a dekóduje GRIB. Ďalšie sú z cache. |
| Žiadne dáta / SSL chyba | Server zámerne ignoruje neúplný TLS reťazec SHMÚ; ak píše DNS/timeout, skontroluj internet. |

---

## Ako to funguje (technicky)

Dáta SHMÚ sú v binárnom formáte **GRIB2** (gridované polia nad SR) — prehliadač ich
sám dekódovať nevie. Preto Python server:

1. nájde **najnovší beh modelu** (4× denne: 00/06/12/18 UTC),
2. stiahne hodinové GRIB súbory (predvolene 0–72 h) do `.grib_cache\`,
3. dekóduje **bodovú predpoveď** pre 10 miest (najbližší bod gridu) cez ecCodes,
4. servíruje JSON na `/api/forecast`,
5. `index.html` to vykreslí a každú hodinu automaticky obnoví.

Dekódované veličiny: teplota 2 m, vietor a nárazy 10 m, oblačnosť (celková/nízka),
hodinové zrážky, tlak (MSL) a CAPE. Z oblačnosti/zrážok/CAPE sa odvodí ikona počasia.

> **Poznámka k ecCodes na Windows / Python 3.14:** binárny balík `eccodes` na
> PyPI nemá pre 3.14 priloženú C-knižnicu. Server si ju preto požičia z balíka
> `ecmwflibs` cez malý „shim" v `_bootstrap_eccodes()` — netreba conda ani
> systémovú inštaláciu.

## Nastavenia (vrch `forecast_server.py`)

| Premenná | Význam | Predvolené |
|---|---|---|
| `MAX_HOURS` | koľko hodín predpovede načítať (model ide do ~102 h) | `72` |
| `CITIES` | zoznam miest `(názov, lat, lon)` | 10 miest SR |
| `PORT` | port servera | `8765` |
| `REFRESH_SECONDS` | ako často server prebuduje dáta | `3600` (1 h) |

## Závislosti

```
eccodes ecmwflibs findlibs numpy
```

Python 3.11+ (testované na 3.14).
