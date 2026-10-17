# Contrat SAM.gov vérifié

Source officielle GSA consultée le 17 octobre 2026 :
[Get Opportunities Public API](https://open.gsa.gov/api/get-opportunities-public-api/),
[source du document officiel](https://github.com/GSA/open-gsa-redesign/blob/master/_apidocs/get-opportunities-public-api.md).
Le site web était refusé par le proxy ; le fichier du dépôt officiel était accessible
via `raw.githubusercontent.com`.

Le tableau officiel indique pour `offset` : **“Indicates the page index.
Default offset starts with 0”**. La réponse définit également `offset` comme
**“Page index specified by a user.”**

La roadmap demandait `offset += 1000` ; cette instruction contredit le contrat officiel.
L'implémentation utilise `offset=0,1,2,...`, avec `limit=1000`, et s'arrête quand
`(offset+1)*limit >= totalRecords`. Les régressions vérifient cinq pages pour 4 200
résultats et six pages pour 5 200, y compris la dernière page partielle.

Autres éléments confirmés : `limit <= 1000` ; `postedFrom`/`postedTo` au format
MM/dd/yyyy ; plage d'au plus un an ; `ptype=o` pour Solicitation et `k` pour Combined
Synopsis/Solicitation. La source consultée ne documente pas `is_active` : ce paramètre
n'est pas envoyé. Le filtre local sur l'activité reste obligatoire.
Le quota dépend du rôle de la clé ; aucun quota réel n'a été mesuré dans cette instance.
La combinaison `ptype=o,k` a été validée par une synchronisation réelle le 17 octobre 2026. Aucune autre adresse ne
reçoit la clé SAM et les redirections de descriptions ne sont pas suivies.
