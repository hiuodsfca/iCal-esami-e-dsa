import re
import hashlib
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

URL = "https://my.liuc.it/calend/Esami.asp?COD=L09B&AN=2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
PRENOTAZIONE_REGEX = re.compile(r"dal\s+(\d{2}/\d{2}/\d{4})\s+al\s+(\d{2}/\d{2}/\d{4})")
CORSO_REGEX = re.compile(r"^(.*?)(?:\s*\(([^)]+)\))?$")
TZ_ROME = ZoneInfo("Europe/Rome")

def calcola_scadenza_dsa(data_esame: date, giorni_lavorativi: int = 15) -> date:
    """Sottrae N giorni lavorativi (salta sabato e domenica) dalla data dell'esame."""
    giorni_sottratti = 0
    data_corrente = data_esame
    
    while giorni_sottratti < giorni_lavorativi:
        data_corrente -= timedelta(days=1)
        # weekday(): 0=Lunedì ... 5=Sabato, 6=Domenica
        if data_corrente.weekday() < 5:
            giorni_sottratti += 1
            
    return data_corrente

def generate_uid(identifier: str) -> str:
    """Genera un UID stabile basato su una stringa univoca dell'evento."""
    return hashlib.sha256(identifier.encode('utf-8')).hexdigest() + "@liuc-scraper.local"

def genera_calendario():
    response = requests.get(URL, headers=HEADERS, timeout=15)
    response.raise_for_status()
    if response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = "iso-8859-1"
        
    soup = BeautifulSoup(response.text, "lxml")
    tabella = soup.find("table", class_="tabella")
    if not tabella:
        raise ValueError("Tabella non trovata")

    cal = Calendar()
    cal.add('prodid', '-//Scraper Esami LIUC//Andrea V.//IT')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'Appelli Ingegneria Gestionale (2° Anno)')
    cal.add('x-wr-timezone', 'Europe/Rome')

    righe = tabella.find_all("tr", class_=re.compile(r"myTb[12]"))

    for riga in righe:
        celle = riga.find_all("td")
        if len(celle) < 5:
            continue

        raw_insegnamento = celle[0].get_text(strip=True)
        prova = celle[1].get_text(strip=True)
        raw_data = celle[2].get_text(strip=True)
        raw_ora = celle[3].get_text(strip=True)
        raw_prenotazione = celle[4].get_text(strip=True)

        match_corso = CORSO_REGEX.match(raw_insegnamento)
        nome_corso = match_corso.group(1).strip() if match_corso else raw_insegnamento
        codice_corso = match_corso.group(2).strip() if match_corso and match_corso.group(2) else "Sconosciuto"

        # 1. Parsing Data Esame
        has_time = False
        dt_esame = None
        if raw_data:
            if raw_ora:
                try:
                    dt_esame = datetime.strptime(f"{raw_data} {raw_ora}", "%d/%m/%Y %H:%M").replace(tzinfo=TZ_ROME)
                    has_time = True
                except ValueError:
                    dt_esame = datetime.strptime(raw_data, "%d/%m/%Y").date()
            else:
                dt_esame = datetime.strptime(raw_data, "%d/%m/%Y").date()

        if not dt_esame:
            continue # Salta se non c'è una data

        # 2. Parsing Prenotazione
        scadenza_prenotazione = None
        pren_match = PRENOTAZIONE_REGEX.search(raw_prenotazione)
        if pren_match:
            scadenza_prenotazione = datetime.strptime(pren_match.group(2), "%d/%m/%Y").date()

        # --- CREAZIONE EVENTI ICAL ---
        base_id = f"{codice_corso}-{raw_data}-{prova}"

        # Evento A: L'Esame
        event_esame = Event()
        event_esame.add('uid', generate_uid(f"ESAME-{base_id}"))
        event_esame.add('summary', f"Esame {nome_corso}")
        event_esame.add('description', f"Prova: {prova}")
        event_esame.add('dtstamp', datetime.now(TZ_ROME))
        
        if has_time:
            event_esame.add('dtstart', dt_esame)
            event_esame.add('dtend', dt_esame + timedelta(hours=1)) # Dura 1 ora di default
        else:
            event_esame.add('dtstart', dt_esame) # Evento tutto il giorno
            
        cal.add_component(event_esame)

        # Evento B: Scadenza DSA (15 gg lavorativi prima)
        data_base_calcolo = dt_esame.date() if has_time else dt_esame
        data_dsa = calcola_scadenza_dsa(data_base_calcolo)
        
        event_dsa = Event()
        event_dsa.add('uid', generate_uid(f"DSA-{base_id}"))
        event_dsa.add('summary', f"Scadenza DSA: {nome_corso}")
        event_dsa.add('description', f"Ultimo giorno utile per inviare la richiesta di misure equipollenti per la prova: {prova}.")
        event_dsa.add('dtstart', data_dsa)
        event_dsa.add('dtstamp', datetime.now(TZ_ROME))
        cal.add_component(event_dsa)

        # Evento C: Scadenza Iscrizione
        if scadenza_prenotazione:
            event_iscr = Event()
            event_iscr.add('uid', generate_uid(f"ISCR-{base_id}"))
            event_iscr.add('summary', f"Scadenza Iscrizione: {nome_corso}")
            event_iscr.add('description', f"Ultimo giorno per iscriversi alla prova: {prova}.")
            event_iscr.add('dtstart', scadenza_prenotazione)
            event_iscr.add('dtstamp', datetime.now(TZ_ROME))
            cal.add_component(event_iscr)

    # Scrive il file su disco
    with open('appelli_liuc.ics', 'wb') as f:
        f.write(cal.to_ical())
    print("Calendario generato con successo: appelli_liuc.ics")

if __name__ == "__main__":
    genera_calendario()
