import sys
import re
import hashlib
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

URL = "https://my.liuc.it/calend/Esami.asp?COD=L09B&AN=2"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

PRENOTAZIONE_REGEX = re.compile(r"dal\s+(\d{2}/\d{2}/\d{4})\s+al\s+(\d{2}/\d{2}/\d{4})")
CORSO_REGEX = re.compile(r"^(.*?)(?:\s*\(([^)]+)\))?$")
TZ_ROME = ZoneInfo("Europe/Rome")


def calcola_scadenza_dsa(data_esame: date, giorni_lavorativi: int = 15) -> date:
    """Sottrae 15 giorni lavorativi saltando sabati e domeniche."""
    giorni_sottratti = 0
    data_corrente = data_esame

    while giorni_sottratti < giorni_lavorativi:
        data_corrente -= timedelta(days=1)
        if data_corrente.weekday() < 5:  # 0=Lunedì ... 4=Venerdì
            giorni_sottratti += 1

    return data_corrente


def generate_uid(identifier: str) -> str:
    """Genera un identificatore univoco deterministico e persistente."""
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"{digest}@liuc-scraper.local"


def main():
    try:
        response = requests.get(URL, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Errore durante il download della pagina LIUC: {e}")
        sys.exit(1)

    if response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = "iso-8859-1"

    soup = BeautifulSoup(response.text, "lxml")
    tabella = soup.find("table", class_="tabella")

    if not tabella:
        print("Errore: Tabella appelli non trovata nel codice HTML.")
        sys.exit(1)

    cal = Calendar()
    cal.add("prodid", "-//Scraper Esami LIUC//Andrea V.//IT")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Appelli Ingegneria Gestionale (2° Anno)")
    cal.add("x-wr-timezone", "Europe/Rome")

    righe = tabella.find_all("tr", class_=re.compile(r"myTb[12]"))
    eventi_creati = 0

    for riga in righe:
        celle = riga.find_all("td")
        if len(celle) < 5:
            continue

        raw_insegnamento = celle[0].get_text(strip=True)
        prova = celle[1].get_text(strip=True)
        raw_data = celle[2].get_text(strip=True)
        raw_ora = celle[3].get_text(strip=True)
        raw_prenotazione = celle[4].get_text(strip=True)

        if not raw_data:
            continue

        match_corso = CORSO_REGEX.match(raw_insegnamento)
        nome_corso = match_corso.group(1).strip() if match_corso else raw_insegnamento
        codice_corso = match_corso.group(2).strip() if (match_corso and match_corso.group(2)) else "CORSO"

        has_time = False
        if raw_ora:
            try:
                dt_esame = datetime.strptime(f"{raw_data} {raw_ora}", "%d/%m/%Y %H:%M").replace(tzinfo=TZ_ROME)
                has_time = True
            except ValueError:
                dt_esame = datetime.strptime(raw_data, "%d/%m/%Y").date()
        else:
            dt_esame = datetime.strptime(raw_data, "%d/%m/%Y").date()

        scadenza_prenotazione = None
        pren_match = PRENOTAZIONE_REGEX.search(raw_prenotazione)
        if pren_match:
            scadenza_prenotazione = datetime.strptime(pren_match.group(2), "%d/%m/%Y").date()

        base_id = f"{codice_corso}-{raw_data}-{prova}"
        now_ts = datetime.now(TZ_ROME)

        # 1. Evento Esame
        event_esame = Event()
        event_esame.add("uid", generate_uid(f"ESAME-{base_id}"))
        event_esame.add("summary", f"Esame {nome_corso}")
        event_esame.add("description", f"Prova: {prova}")
        event_esame.add("dtstamp", now_ts)

        if has_time:
            event_esame.add("dtstart", dt_esame)
            event_esame.add("dtend", dt_esame + timedelta(hours=1))
        else:
            event_esame.add("dtstart", dt_esame)

        cal.add_component(event_esame)
        eventi_creati += 1

        # 2. Evento Scadenza DSA
        data_base = dt_esame.date() if has_time else dt_esame
        data_dsa = calcola_scadenza_dsa(data_base)

        event_dsa = Event()
        event_dsa.add("uid", generate_uid(f"DSA-{base_id}"))
        event_dsa.add("summary", f"Scadenza DSA: {nome_corso}")
        event_dsa.add("description", f"Termine invio richiesta misure equipollenti (15 gg lavorativi). Prova: {prova}.")
        event_dsa.add("dtstart", data_dsa)
        event_dsa.add("dtstamp", now_ts)
        cal.add_component(event_dsa)
        eventi_creati += 1

        # 3. Evento Scadenza Prenotazione
        if scadenza_prenotazione:
            event_iscr = Event()
            event_iscr.add("uid", generate_uid(f"ISCR-{base_id}"))
            event_iscr.add("summary", f"Scadenza Iscrizione: {nome_corso}")
            event_iscr.add("description", f"Ultimo giorno per registrarsi all'appello. Prova: {prova}.")
            event_iscr.add("dtstart", scadenza_prenotazione)
            event_iscr.add("dtstamp", now_ts)
            cal.add_component(event_iscr)
            eventi_creati += 1

    if eventi_creati == 0:
        print("Nessun evento generato: possibile anomalia nel portale. Interrompo.")
        sys.exit(1)

    with open("appelli_liuc.ics", "wb") as f:
        f.write(cal.to_ical())

    print(f"Sincronizzazione completata: generati {eventi_creati} eventi totali.")


if __name__ == "__main__":
    main()
