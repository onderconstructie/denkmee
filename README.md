# Denk mee met Mechelen

Wat de Mechelse politiek beslist, in mensentaal en altijd met de bron erbij.

**[denkmee.asgaupaust.be](https://denkmee.asgaupaust.be)**

Hoe het werkt en waar het tekortschiet, staat op de [techniekpagina](https://denkmee.asgaupaust.be/techniek/).

## Zelf draaien

```
python -m pip install requests anthropic
python run_all.py
```

De pijplijn haalt de openbare stukken op, vat ze samen met AI en bouwt de site in `dist/`.
Voor de samenvattingen heb je een eigen Anthropic-sleutel nodig (`ANTHROPIC_API_KEY`).
De scripts staan in `pijplijn/` en leggen in hun docstring uit wat ze doen. Elke push naar `main` zet `dist/` online.

Onderdeel van [As Gau Paust](https://asgaupaust.be) · [Privacy](https://asgaupaust.be/privacy/)
