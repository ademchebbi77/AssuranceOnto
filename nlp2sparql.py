# nlp2sparql.py  —  AssuranceOnto : question en français -> requête SPARQL (sans LLM)
# Outils : spaCy (fr_core_news_sm : tokenisation, lemmatisation, POS), rdflib (lecture de l'ontologie), regex,
#          difflib (correction des fautes de frappe sur les noms d'individus)
import difflib
import re
import unicodedata
import spacy
from rdflib import RDF, RDFS, OWL, URIRef

NS = "http://www.example.org/assuranceonto#"
PREFIXES = f"""PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ns: <{NS}>
"""

nlp = spacy.load("fr_core_news_sm")


def plain(s):
    """minuscules sans accents (garde les espaces)"""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm(s):
    """'Ahmed_BenSalah' -> 'ahmedbensalah'"""
    return re.sub(r"[^a-z0-9]", "", plain(s))


def local(u):
    return str(u).split("#")[-1]


STOP = {"de", "du", "des", "la", "le", "les", "l", "d", "un", "une", "et", "a", "au", "aux", "qui", "que", "est", "sont",
        "quel", "quels", "quelle", "quelles", "il", "elle", "ils", "elles", "ce", "ces", "cet", "cette", "son", "sa", "ses",
        "leur", "leurs", "par", "pour", "sur", "avec", "dans", "y", "t", "s", "on", "ont", "etre", "il", "lui"}

# ================= VOCABULAIRE PROPRE À ASSURANCEONTO (à adapter si l'ontologie change) =================
CLASS_SYN = {"contrat": "ContratAssurance", "police": "ContratAssurance",
             "contratautomobile": "ContratAuto", "assuranceauto": "ContratAuto", "assuranceautomobile": "ContratAuto",
             "assurancehabitation": "ContratHabitation", "assurancesante": "ContratSante", "assurancevie": "ContratVie",
             "contratrc": "ContratResponsabiliteCivile", "responsabilitecivile": "ContratResponsabiliteCivile",
             "premium": "ContratPremium",
             "client": "Assure", "souscripteur": "Assure", "particulier": "AssureParticulier",
             "entreprise": "AssureEntreprise", "societe": "AssureEntreprise",
             "compagnie": "Assureur", "compagnieassurance": "Assureur",
             "voiture": "Vehicule", "maison": "Logement", "appartement": "Logement", "bien": "BienAssure",
             "clause": "ClauseLegale", "exclusion": "ClauseExclusion", "resiliation": "ClauseResiliation",
             "subrogation": "ClauseSubrogation",
             "bareme": "BaremeIndemnisation", "grille": "BaremeIndemnisation",
             "indemnite": "Indemnisation", "remboursement": "Indemnisation",
             "cotisation": "Prime",
             "accident": "Sinistre", "sinistreautomobile": "SinistreAuto", "sinistreexpertiser": "SinistreAExpertiser",
             "couvert": "SinistreCouvert", "exclu": "SinistreExclu", "grave": "SinistreGrave", "leger": "SinistreLeger",
             "obligatoire": "GarantieObligatoire", "optionnelle": "GarantieOptionnelle", "optionnel": "GarantieOptionnelle",
             "materiel": "DommageMateriel", "corporel": "DommageCorporel", "immateriel": "DommageImmateriel"}

ALIAS_PREFIX = {"vehicule", "logement", "expert", "societe", "dommage"}     # Vehicule_Clio -> "clio" ; Dommage_Vol -> "vol"

# indice -> propriété (str) ou famille de propriétés (liste : on garde celle qui convient à la classe / à l'individu)
PROP_CUES = {"age": "age", "an": "age", "ans": "age",
             "montant": ["montantEstime", "montantVerse", "montantPrime"], "cout": ["montantEstime", "montantVerse", "montantPrime"],
             "somme": ["montantEstime", "montantVerse", "montantPrime"], "tnd": ["montantEstime", "montantVerse", "montantPrime", "valeurBien"],
             "dinar": ["montantEstime", "montantVerse", "montantPrime", "valeurBien"], "dinars": ["montantEstime", "montantVerse", "montantPrime", "valeurBien"],
             "estime": "montantEstime", "verse": "montantVerse",
             "plafond": ["plafondGarantie", "plafondIndemnisation"], "franchise": "franchise",
             "gravite": "niveauGravite", "niveau": "niveauGravite", "taux": "tauxIndemnisation", "pourcentage": "tauxIndemnisation",
             "valeur": "valeurBien", "immatriculation": "immatriculation", "matricule": "immatriculation", "plaque": "immatriculation",
             "surface": "surfaceM2", "m2": "surfaceM2", "periodicite": "periodicite", "frequence": "periodicite",
             "numero": "numeroContrat",
             "date": ["dateSinistre", "dateEffet", "dateVersement", "dateDeclaration", "dateEcheance"],
             "effet": "dateEffet", "echeance": "dateEcheance", "expiration": "dateEcheance",
             "declaration": "dateDeclaration", "versement": "dateVersement",
             "nom": ["nomAssure", "nomExpert", "raisonSociale"], "raison": "raisonSociale",
             "libelle": "libelleClause", "texte": "libelleClause", "reference": "referenceLegale", "article": "referenceLegale"}

DEFAULT_NUM = {"AssureParticulier": "age", "Assure": "age", "Sinistre": "montantEstime", "Indemnisation": "montantVerse",
               "Garantie": "plafondGarantie", "Prime": "montantPrime", "BienAssure": "valeurBien",
               "Dommage": "niveauGravite", "BaremeIndemnisation": "tauxIndemnisation"}
DEFAULT_DATE = {"Sinistre": "dateSinistre", "ContratAssurance": "dateEffet", "Indemnisation": "dateVersement"}
NAME_PROP = {"Assure": "nomAssure", "Assureur": "raisonSociale", "Expert": "nomExpert"}
VALUE_PROPS = {"periodicite"}                        # valeurs texte utilisables comme filtre (Annuelle, Mensuelle)

# classes définies (OWL) et classes inférées par les règles SWRL : rdflib n'a pas de raisonneur,
# on réécrit donc leur définition en SPARQL (variable ?x)
DEFINED = {
    "SinistreGrave": "?x ns:montantEstime ?mt . FILTER (?mt >= 10000)",
    "SinistreAExpertiser": "?x ns:montantEstime ?mt . FILTER (?mt >= 10000)",                     # R3
    "SinistreLeger": "?x ns:montantEstime ?mt . FILTER (?mt < 10000)",
    "SinistreCouvert": "?x ns:concerneContrat ?ct . ?ct ns:comporteGarantie|^ns:estIncluseDans ?gt . "
                       "?gt ns:couvreDommage ?dt . ?x ns:causeDommage ?dt",                       # R1
    "SinistreExclu": "?x ns:concerneContrat ?ct . ?ct ns:contientClause|^ns:estClauseDe ?cl . "
                     "?cl ns:excluDommage ?dt . ?x ns:causeDommage ?dt",                          # R2
    "ContratPremium": "?x ns:comporteGarantie|^ns:estIncluseDans ?g1, ?g2, ?g3 . "
                      "FILTER (?g1 != ?g2 && ?g1 != ?g3 && ?g2 != ?g3)",                          # >= 3 garanties
}

# chemins réutilisés (propriété OU son inverse : l'ontologie déclare les inverses mais rdflib n'infère pas)
SOUS = "(ns:souscrit|^ns:souscritPar)"
EMIS = "(ns:emisPar|^ns:emet)"
GAR = "(ns:comporteGarantie|^ns:estIncluseDans)"
CLA = "(ns:contientClause|^ns:estClauseDe)"
DECL = "(ns:declare|^ns:declarePar)"
INDEM = "(ns:donneLieuA|^ns:resulteDe)"

# groupe de relations : (radicaux des mots-clés, règles)
# règle = (type de l'individu cité, motif SPARQL avec {I}, type du résultat)
RELATIONS = {
    "souscrit": (("souscri", "detien", "possed", "abonn"), [
        ("Assure", "{I} " + SOUS + " ?x", "ContratAssurance"),
        ("ContratAssurance", "?x " + SOUS + " {I}", "Assure")]),
    "emet": (("emet", "emis", "emettr", "deliv"), [
        ("ContratAssurance", "{I} " + EMIS + " ?x", "Assureur"),
        ("Assureur", "?x " + EMIS + " {I}", "ContratAssurance")]),
    "client": (("client", "chez", "gere", "gerer", "gest"), [                  # chaînes estClientDe et gerePar
        ("Assure", "{I} " + SOUS + "/" + EMIS + " ?x", "Assureur"),
        ("Assureur", "?x " + SOUS + "/" + EMIS + " {I}", "Assure"),
        ("Sinistre", "{I} ns:concerneContrat/" + EMIS + " ?x", "Assureur"),
        ("Assureur", "?x ns:concerneContrat/" + EMIS + " {I}", "Sinistre")]),
    "garantie": (("garanti", "comport", "inclu"), [
        ("ContratAssurance", "{I} " + GAR + " ?x", "Garantie"),
        ("Garantie", "?x " + GAR + " {I}", "ContratAssurance"),
        ("Assure", "{I} " + SOUS + "/" + GAR + " ?x", "Garantie")]),
    "clause": (("clause", "contien", "stipul"), [
        ("ContratAssurance", "{I} " + CLA + " ?x", "ClauseLegale"),
        ("ClauseLegale", "?x " + CLA + " {I}", "ContratAssurance")]),
    "couvre": (("couvr", "proteg"), [
        ("ContratAssurance", "{I} ns:couvreBien ?x", "BienAssure"),
        ("BienAssure", "?x ns:couvreBien {I}", "ContratAssurance"),
        ("Garantie", "{I} ns:couvreDommage ?x", "Dommage"),
        ("Dommage", "?x ns:couvreDommage {I}", "Garantie"),
        ("ContratAssurance", "{I} " + GAR + "/ns:couvreDommage ?x", "Dommage"),
        ("Dommage", "?x " + GAR + "/ns:couvreDommage {I}", "ContratAssurance"),
        ("Assure", "{I} " + SOUS + "/ns:couvreBien ?x", "BienAssure"),
        ("BienAssure", "?x " + SOUS + "/ns:couvreBien {I}", "Assure")]),
    "declare": (("declar", "signal"), [
        ("Assure", "{I} " + DECL + " ?x", "Sinistre"),
        ("Sinistre", "?x " + DECL + " {I}", "Assure")]),
    "concerne": (("concern", "rattach"), [
        ("ContratAssurance", "?x ns:concerneContrat {I}", "Sinistre"),
        ("Sinistre", "{I} ns:concerneContrat ?x", "ContratAssurance")]),
    "cause": (("caus", "provoq", "entrain"), [
        ("Sinistre", "{I} ns:causeDommage ?x", "Dommage"),
        ("Dommage", "?x ns:causeDommage {I}", "Sinistre")]),
    "exclu": (("exclu",), [
        ("ClauseLegale", "{I} ns:excluDommage ?x", "Dommage"),
        ("Dommage", "?x ns:excluDommage {I}", "ClauseLegale"),
        ("ContratAssurance", "{I} " + CLA + "/ns:excluDommage ?x", "Dommage"),
        ("Dommage", "?x " + CLA + "/ns:excluDommage {I}", "ContratAssurance")]),
    "expertise": (("expertis", "evalu"), [
        ("Sinistre", "{I} ns:expertisePar ?x", "Expert"),
        ("Expert", "?x ns:expertisePar {I}", "Sinistre")]),
    "emploie": (("emploi", "travail", "embauch"), [
        ("Assureur", "{I} ns:emploieExpert ?x", "Expert"),
        ("Expert", "?x ns:emploieExpert {I}", "Assureur")]),
    "indemnise": (("indemn", "rembours", "pay", "pai"), [
        ("Sinistre", "{I} " + INDEM + " ?x", "Indemnisation"),
        ("Indemnisation", "?x " + INDEM + " {I}", "Sinistre"),
        ("Assure", "{I} " + DECL + "/" + INDEM + " ?x", "Indemnisation")]),
    "bareme": (("bareme", "calcul", "applic", "grille"), [
        ("Indemnisation", "{I} ns:selonBareme ?x", "BaremeIndemnisation"),
        ("BaremeIndemnisation", "?x ns:selonBareme {I}", "Indemnisation"),
        ("BaremeIndemnisation", "{I} ns:baremePourDommage ?x", "Dommage"),
        ("Dommage", "?x ns:baremePourDommage {I}", "BaremeIndemnisation"),
        ("Sinistre", "{I} " + INDEM + "/ns:selonBareme ?x", "BaremeIndemnisation")]),
    "prime": (("prime", "cotis"), [
        ("ContratAssurance", "{I} ns:aPrime ?x", "Prime"),
        ("Prime", "?x ns:aPrime {I}", "ContratAssurance"),
        ("Assure", "{I} " + SOUS + "/ns:aPrime ?x", "Prime")]),
    "coassure": (("coassur",), [
        ("Assure", "{I} ns:coAssureAvec|^ns:coAssureAvec ?x", "Assure")]),
}
# =====================================================================================================

ADD = {"ajouter", "ajoute", "creer", "cree", "inserer", "insere", "ajout", "enregistrer", "enregistre"}
DEL = {"supprimer", "supprime", "effacer", "efface", "retirer", "retire", "enlever", "enleve"}
UPD = {"modifier", "modifie", "changer", "change", "mettre", "mets", "actualiser", "actualise", "corriger", "corrige"}
COUNT = {"combien", "nombre", "compter"}
SEARCH = {"chercher", "cherche", "rechercher", "recherche", "trouver", "trouve"}
DESCR = {"information", "informations", "detail", "details", "decrire", "decris", "montrer", "montre", "afficher", "affiche"}

COMPARATORS = [("aumoins", ">="), ("auplus", "<="), ("plusde", ">"), ("plusque", ">"), ("superieur", ">"),
               ("depasse", ">"), ("audessus", ">"), ("moinsde", "<"), ("moinsque", "<"), ("inferieur", "<"),
               ("endessous", "<"), ("exactement", "="), ("egala", "="), ("avant", "<"), ("apres", ">"), ("depuis", ">=")]
DESC_WORDS = ("plusage", "plusvieux", "pluseleve", "plusgrand", "pluscher", "pluscouteux", "plusgrave", "plusimportant",
              "maximum", "maximal", "plusrecent")
ASC_WORDS = ("plusjeune", "plusfaible", "pluspetit", "moinscher", "moinscouteux", "moinsgrave", "minimum", "minimal",
             "moinseleve", "plusancien")


class Translator:
    def __init__(self, graph):
        g = self.g = graph
        self.classes = {local(c) for c in g.subjects(RDF.type, OWL.Class) if str(c).startswith(NS)}
        self.cls_keys = {norm(c): c for c in self.classes}
        for k, v in CLASS_SYN.items():
            if v in self.classes:
                self.cls_keys.setdefault(k, v)
        # individus : nom local -> ensemble de ses classes (superclasses incluses)
        self.itypes = {}
        for s, t in g.subject_objects(RDF.type):
            if str(s).startswith(NS) and local(t) in self.classes:
                self.itypes.setdefault(local(s), set()).update(self.supers(local(t)))
        self.ind_keys = {}
        for name in self.itypes:
            self.ind_keys[norm(name)] = name
        for name in self.itypes:                          # alias : Vehicule_Clio -> clio ; Ahmed_BenSalah -> ahmed ; Sinistre_001 -> sinistre1
            parts = name.split("_")
            first, rest = norm(parts[0]), norm("".join(parts[1:]))
            aliases = []
            if rest and first in ALIAS_PREFIX:
                aliases += [rest, re.sub(r"\d+$", "", rest)]
            elif rest and first == "g":
                aliases.append("garantie" + rest)
            elif rest and len(first) >= 3 and first not in self.cls_keys:
                aliases.append(first)
            if len(parts) > 1 and parts[-1].isdigit():
                aliases.append(norm("".join(parts[:-1])) + str(int(parts[-1])))
            for a in aliases:
                if len(a) >= 3 and a not in self.cls_keys:
                    self.ind_keys.setdefault(a, name)
        # propriétés de données : nom -> type (integer, decimal, string, dateTime) et domaine
        self.dprops, self.pdom = {}, {}
        for p in g.subjects(RDF.type, OWL.DatatypeProperty):
            if str(p).startswith(NS):
                self.dprops[local(p)] = local(next(g.objects(p, RDFS.range), "string"))
                self.pdom[local(p)] = local(next(g.objects(p, RDFS.domain), ""))
        # propriétés objet directes (sans les chaînes) : (domaine, codomaine, nom) — utilisées pour les ajouts
        self.oprops = []
        for p in sorted(g.subjects(RDF.type, OWL.ObjectProperty)):
            if str(p).startswith(NS) and not any(g.objects(p, OWL.propertyChainAxiom)):
                d, r = next(g.objects(p, RDFS.domain), None), next(g.objects(p, RDFS.range), None)
                if d is not None and r is not None:
                    self.oprops.append((local(d), local(r), local(p)))
        # valeurs texte connues (Annuelle, Mensuelle...) pour filtrer
        self.values = {}
        for p in VALUE_PROPS & set(self.dprops):
            for o in g.objects(None, URIRef(NS + p)):
                v = str(o)
                for k in {norm(v), norm(v).rstrip("e")}:
                    self.values.setdefault(k, (p, v))

    def supers(self, c):
        return {local(x) for x in self.g.transitive_objects(URIRef(NS + c), RDFS.subClassOf)
                if str(x).startswith(NS)} | {c}

    def compatible(self, a, b):
        """a et b sont sur la même branche de la hiérarchie (l'une est sous-classe de l'autre)"""
        return a in self.supers(b) or b in self.supers(a)

    def type_pattern(self, cls):
        base = f"?x rdf:type/rdfs:subClassOf* ns:{cls} ."
        if cls in DEFINED:
            return f"{{ {base} }} UNION {{ {DEFINED[cls]} }}"
        return base

    # ------------------------------------------------------------------ analyse linguistique
    def _grams(self, seqs, raw, used):
        """repère individus (I) et classes (C), les plus longues expressions d'abord"""
        found = []
        for n in (3, 2, 1):
            for i in range(len(raw) - n + 1):
                if any(j in used for j in range(i, i + n)):
                    continue
                hit = None
                for seq in seqs:
                    key = "".join(seq[i:i + n])
                    for k in (key, key[:-1] if key.endswith("s") else key):   # pluriel : contrats -> contrat
                        ind, cls = self.ind_keys.get(k), self.cls_keys.get(k)
                        if ind and (not cls or (n == 1 and "_" in raw[i])):   # "Clause_Subrogation" écrit tel quel = individu
                            hit = ("I", ind)
                        elif cls:
                            hit = ("C", cls)
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    found.append((i, n, hit))
                    used.update(range(i, i + n))
        return sorted(found)

    def _pick_class(self, classes):
        """première classe citée, ou la plus précise si plusieurs sont sur la même branche (sinistre ... grave)"""
        if not classes:
            return None
        best = classes[0]
        for c in classes[1:]:
            if best in self.supers(c):
                best = c
        return best

    def _resolve(self, cue, info):
        """famille de propriétés -> celle dont le domaine convient à la classe ou à l'individu cité"""
        if isinstance(cue, str):
            return cue
        ctx = ([info["classe"]] if info["classe"] else []) + [t for i in info["individus"] for t in self.itypes[i]]
        for c in ctx:
            for p in cue:
                if p in self.pdom and self.compatible(self.pdom[p], c):
                    return p
        return cue[0]

    def analyse(self, text):
        doc = nlp(text)
        toks = [t for t in doc if norm(t.text) and norm(t.text) not in STOP]
        W = [norm(t.text) for t in toks]
        L = [norm(t.lemma_) for t in toks]
        S = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in W]      # pluriel mot à mot
        R = [t.text for t in toks]
        flat = norm(text)
        both = set(W) | set(L)
        info = {"intent": "select", "classe": None, "individus": [], "inconnus": [], "proprietes": [],
                "relations": [], "filtres": [], "tri": None}
        for key, intents in (("insert", ADD), ("delete", DEL), ("update", UPD), ("search", SEARCH),
                             ("count", COUNT), ("describe", DESCR)):
            if both & intents:
                info["intent"] = key
                break
        used = set()
        found = self._grams((W, L, S), R, used)
        info["corrections"] = []
        if info["intent"] in ("select", "count", "describe", "search"):   # jamais pour ajouter / modifier / supprimer
            for k, t in enumerate(toks):                 # fautes de frappe : "Ahmd" -> Ahmed_BenSalah (difflib)
                if k not in used and t.i > 0 and t.text[:1].isupper() and len(W[k]) >= 4:
                    m = difflib.get_close_matches(W[k], list(self.ind_keys), n=1, cutoff=0.75)
                    if m:
                        found.append((k, 1, ("I", self.ind_keys[m[0]])))
                        used.add(k)
                        info["corrections"].append(f"{t.text} → {self.ind_keys[m[0]]}")
            found.sort()
        info["individus"] = list(dict.fromkeys(v for _, _, (k, v) in found if k == "I"))
        info["classe"] = self._pick_class([v for _, _, (k, v) in found if k == "C"])
        used_ind = {j for i, n, (k, _) in found if k == "I" for j in range(i, i + n)}   # positions prises par des individus
        # propriétés (dans l'ordre) : une propriété précise l'emporte sur une famille générique de la même sorte
        specific, generic, cue_pos = [], [], set()
        for k in range(len(W)):
            if k in used:
                continue
            cue = PROP_CUES.get(W[k]) or PROP_CUES.get(L[k]) or PROP_CUES.get(S[k])
            if cue:
                cue_pos.add(k)
                (specific if isinstance(cue, str) else generic).append(self._resolve(cue, info))
        for p in generic:
            if not any(s[:4] == p[:4] for s in specific):
                specific.append(p)
        info["proprietes"] = [p for p in dict.fromkeys(specific)
                              if not info["classe"] or self.compatible(self.pdom.get(p, ""), info["classe"])
                              or info["individus"]]
        # relations (groupes de mots-clés, dans l'ordre d'apparition)
        rel_pos = set()
        for k, (w, l) in enumerate(zip(W, L)):
            if k in used_ind or k in cue_pos:
                continue
            for grp, (stems, _) in RELATIONS.items():
                if any(w.startswith(s) or l.startswith(s) for s in stems):
                    rel_pos.add(k)
                    if grp not in info["relations"]:
                        info["relations"].append(grp)
        # valeurs texte (Annuelle, Mensuelle...)
        vused = set(used)
        vals = {}
        for k in range(len(W)):
            for seq in (W, L, S):
                if k not in vused and seq[k] in self.values:
                    vals[k] = self.values[seq[k]]
                    vused.add(k)
        for k in sorted(vals):
            info["filtres"].append((vals[k][0], "=", vals[k][1]))
        used |= vused
        # nombres (hors ceux appartenant à un nom d'individu)
        nums = [t.text.replace(",", ".") for k, t in enumerate(toks)
                if k not in used and re.fullmatch(r"\d+([.,]\d+)?", t.text)]
        info["nombres"] = nums
        info["_text"] = (text, flat, nums)
        # inconnus : noms propres non reconnus
        for k, t in enumerate(toks):
            if t.i > 0 and t.pos_ == "PROPN" and k not in used and k not in cue_pos \
                    and not any(x in self.cls_keys for x in (W[k], L[k], S[k])):
                info["inconnus"].append(t.text)
        # superlatifs
        if any(w in flat for w in DESC_WORDS): info["tri"] = "DESC"
        if any(w in flat for w in ASC_WORDS): info["tri"] = "ASC"
        # rôle de chaque mot (affiché en couleur dans l'interface)
        cls_pos = {j for i, n, (k, _) in found if k == "C" for j in range(i, i + n)}
        intents = ADD | DEL | UPD | COUNT | SEARCH | DESCR
        role = {}
        for k in range(len(toks)):
            role[k] = ("individu" if k in used_ind else "classe" if k in cls_pos else "propriete" if k in cue_pos
                       else "valeur" if k in vals else "relation" if k in rel_pos
                       else "intention" if W[k] in intents or L[k] in intents
                       else "nombre" if re.fullmatch(r"\d+([.,]\d+)?", toks[k].text) else "")
        kmap = {t.i: k for k, t in enumerate(toks)}
        info["tokens"] = [{"t": t.text, "lemme": t.lemma_, "pos": t.pos_,
                           "role": role[kmap[t.i]] if t.i in kmap else ("" if t.is_punct else "stop")}
                          for t in doc if not t.is_space]
        return info, doc

    def translate(self, text):
        text = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", text.strip())     # "10 000" -> "10000"
        info, doc = self.analyse(text)
        build = {"select": self._select, "count": self._select, "describe": self._select, "insert": self._insert,
                 "delete": self._delete, "update": self._update, "search": self._search}[info["intent"]]
        sparql = build(text, info, doc)
        info.pop("_text", None)
        return sparql, info

    # ------------------------------------------------------------------ helpers
    def _default(self, table, cls):
        if not cls:
            return None
        if cls in table:
            return table[cls]
        for c in self.supers(cls):
            if c in table:
                return table[c]
        return None

    def _num_prop(self, info):
        for p in info["proprietes"]:
            if self.dprops.get(p) in ("integer", "decimal", "int"):
                return p
        return self._default(DEFAULT_NUM, info["classe"])

    def _lit(self, prop, value):
        rng = self.dprops.get(prop, "string")
        v = str(value).replace(",", ".").strip()
        if rng in ("integer", "int"): return f'"{int(float(v))}"^^xsd:integer'
        if rng == "decimal": return f'"{v}"^^xsd:decimal'
        if rng == "dateTime": return f'"{v if "T" in v else v + "T00:00:00"}"^^xsd:dateTime'
        if rng == "date": return f'"{v}"^^xsd:date'
        return '"' + str(value).strip().replace('"', "'") + '"^^xsd:string'

    NOM = 'BIND(REPLACE(STRAFTER(STR(?x), "#"), "_", " ") AS ?nom)'

    # ------------------------------------------------------------------ SELECT / COUNT / DESCRIBE
    def _relation(self, info, count):
        """individu cité + relation (mot-clé, ou à défaut la classe demandée) -> résultats liés"""
        inds, classe = info["individus"], info["classe"]
        if classe and info["relations"] and any(classe in self.itypes[i] for i in inds):
            classe = None                          # "le contrat Contrat_Auto_001 couvre quoi" : la classe décrit l'individu
        groups = list(info["relations"])
        if classe and not any(self.compatible(t, classe) for i in inds for t in self.itypes[i]):
            groups += [g for g in RELATIONS if g not in groups]     # pas de mot-clé : on cherche un lien vers la classe
        for grp in groups:
            for ind in inds:
                rules = [r for r in RELATIONS[grp][1] if r[0] in self.itypes[ind]]
                if classe:
                    rules = [r for r in rules if self.compatible(r[2], classe)]
                if not rules:
                    continue
                blocks = []
                for _, pat, rtype in rules:
                    b = pat.replace("{I}", f"ns:{ind}")
                    if classe and classe != rtype and rtype in self.supers(classe):
                        b += " . " + self.type_pattern(classe)
                    blocks.append(b)
                body = "{ " + " }\n  UNION\n  { ".join(blocks) + " }"
                rcls = classe or rules[0][2]
                extra = [p for p in info["proprietes"] if self.compatible(self.pdom.get(p, ""), rcls)]
                if not extra and self._default(DEFAULT_NUM, rcls) and len({r[2] for r in rules}) == 1:
                    extra = [self._default(DEFAULT_NUM, rcls)]
                if count:
                    extra = []
                lines = [body] + [f"OPTIONAL {{ ?x ns:{p} ?{p} }}" for p in extra] + [self.NOM]
                head = "(COUNT(DISTINCT ?x) AS ?nombre)" if count else "DISTINCT ?nom " + " ".join("?" + p for p in extra)
                return f"{PREFIXES}SELECT {head.strip()} WHERE {{\n  " + "\n  ".join(lines) + "\n}"
        return None

    def _select(self, text, info, doc):
        inds, classe, count = info["individus"], info["classe"], info["intent"] == "count"
        if info["inconnus"] and not inds:
            raise ValueError(f"Individu inconnu dans l'ontologie : {info['inconnus'][0]}")

        # 1) relation entre un individu cité et un résultat : "contrats souscrits par Ahmed"
        if inds and (info["relations"] or classe):
            q = self._relation(info, count)
            if q:
                return q

        # 2) individu(s) + propriété : "montant estimé de Sinistre_001"
        if inds and info["proprietes"]:
            ps = info["proprietes"]
            vals = " ".join(f"ns:{i}" for i in inds)
            w = [f"VALUES ?x {{ {vals} }}"] + [f"OPTIONAL {{ ?x ns:{p} ?{p} }}" for p in ps] + [self.NOM]
            return f"{PREFIXES}SELECT ?nom {' '.join('?' + p for p in ps)} WHERE {{\n  " + "\n  ".join(w) + "\n}"

        # 3) individu + classe : appartenance (classes définies / règles SWRL) "Sinistre_004 est-il exclu ?"
        if inds and classe:
            i = inds[0]
            if not any(self.compatible(t, classe) for t in self.itypes[i]):
                raise ValueError(f"Aucune relation trouvée entre {i} et la classe {classe}.")
            return (f"{PREFIXES}SELECT ?nom ?classe ?reponse WHERE {{\n  VALUES ?x {{ ns:{i} }}\n"
                    f"  BIND (EXISTS {{ {self.type_pattern(classe)} }} AS ?ok)\n"
                    f'  BIND (IF(?ok, "Oui", "Non") AS ?reponse)\n  BIND ("{classe}" AS ?classe)\n  {self.NOM}\n}}')

        # 4) individu seul : toutes ses informations (y compris les liens entrants)
        if inds:
            i = inds[0]
            return (f"{PREFIXES}SELECT ?propriete ?valeur WHERE {{\n"
                    f"  {{ ns:{i} ?propriete ?valeur . FILTER (?valeur != owl:NamedIndividual) }}\n  UNION\n"
                    f'  {{ ?valeur ?p ns:{i} . BIND (CONCAT("(inverse) ", STRAFTER(STR(?p), "#")) AS ?propriete) }}\n}}')

        # 5) liste / filtre / tri / comptage sur une classe
        if not classe:
            raise ValueError("Je n'ai pas compris : précise une classe (contrats, sinistres, assurés, garanties...) ou un nom.")
        w = [self.type_pattern(classe)]
        sel, filt = [], []
        want = [p for p in info["proprietes"] if p in self.dprops]
        nums = list(info["nombres"])
        _, flat, _ = info["_text"]
        dprop = self._default(DEFAULT_DATE, classe)
        year = next((n for n in nums if re.fullmatch(r"(19|20)\d\d", n)), None)
        if year and dprop and not any(self.dprops.get(p) in ("integer", "decimal") for p in want):
            op = next((o for k, o in COMPARATORS if k in flat), "=")
            w.append(f"?x ns:{dprop} ?{dprop} .")
            w.append(f"FILTER (YEAR(?{dprop}) {op} {year})")
            sel.append(f"?{dprop}")
            nums.remove(year)
        m = re.search(r"entre\s+(\d+(?:[.,]\d+)?)\s+et\s+(\d+(?:[.,]\d+)?)", text.lower())
        npf = self._num_prop(info) if (nums or info["tri"]) else None
        if m and npf:
            filt += [(npf, ">=", m.group(1)), (npf, "<=", m.group(2))]
        elif nums and npf:
            op = next((o for k, o in COMPARATORS if k in flat), "=")
            filt.append((npf, op, nums[0]))
        filt += [f for f in info["filtres"]]
        for p, _, _ in filt:
            if p not in want: want.append(p)
        if info["tri"] and npf and npf not in want: want.append(npf)
        for p in want:
            w.append(f"?x ns:{p} ?{p} .")
            sel.append(f"?{p}")
        for p, op, v in filt:
            w.append(f"FILTER (?{p} {op} {v})" if self.dprops.get(p) in ("integer", "decimal", "int")
                     else f'FILTER (STR(?{p}) = "{v}")')
        if not sel and not count and self._default(DEFAULT_NUM, classe):    # colonne utile par défaut
            p = self._default(DEFAULT_NUM, classe)
            w.append(f"OPTIONAL {{ ?x ns:{p} ?{p} }}")
            sel.append(f"?{p}")
        w.append(self.NOM)
        tail = f"\nORDER BY {info['tri']}(?{npf})\nLIMIT 1" if info["tri"] and npf else ""
        head = "(COUNT(DISTINCT ?x) AS ?nombre)" if count else "DISTINCT ?nom " + " ".join(sel)
        return f"{PREFIXES}SELECT {head.strip()} WHERE {{\n  " + "\n  ".join(w) + f"\n}}{tail}"

    def _verif(self, uri):
        return (f"{PREFIXES}SELECT ?propriete ?valeur WHERE {{\n  ns:{uri} ?propriete ?valeur .\n"
                f"  FILTER (?valeur != owl:NamedIndividual)\n}}")

    # ------------------------------------------------------------------ SEARCH : "cherche peugeot"
    def _search(self, text, info, doc):
        words = [t.text for t in doc if norm(t.text) and norm(t.text) not in STOP and norm(t.lemma_) not in SEARCH
                 and norm(t.text) not in SEARCH and norm(t.text) not in self.cls_keys]
        if not words:
            raise ValueError("Écris par ex. « cherche peugeot »")
        term = plain(words[-1]).replace('"', "")
        return (f"{PREFIXES}SELECT DISTINCT ?nom ?classe WHERE {{\n  ?x rdf:type ?c .\n"
                f'  FILTER (STRSTARTS(STR(?c), "{NS}") && STRSTARTS(STR(?x), "{NS}"))\n'
                f'  {{ FILTER (CONTAINS(LCASE(STR(?x)), "{term}")) }}\n  UNION\n'
                f'  {{ ?x ?p ?lit . FILTER (isLiteral(?lit) && CONTAINS(LCASE(STR(?lit)), "{term}")) }}\n'
                f'  {self.NOM}\n  BIND(STRAFTER(STR(?c), "#") AS ?classe)\n}}')

    # ------------------------------------------------------------------ INSERT
    def _insert(self, text, info, doc):
        cls = info["classe"]
        if not cls:
            raise ValueError("Précise la classe : « ajoute un assuré particulier nommé Karim de 40 ans »")
        if cls in DEFINED:
            raise ValueError(f"{cls} est une classe définie/inférée : ajoute plutôt un {cls[:8]}... de base.")
        m = re.search(r"(?:nomm[ée]e?|appel[ée]e?)\s+([A-Za-zÀ-ÿ0-9_]+)", text)
        name = m.group(1) if m else None
        if not name:                                  # sinon : le mot juste après la classe ("ajoute un expert Sami")
            cw = [k for k in self.cls_keys if self.cls_keys[k] == cls]
            for t in doc:
                if norm(t.text) in cw or norm(t.lemma_) in cw:
                    nxt = [x for x in doc[t.i + 1:] if norm(x.text) and norm(x.text) not in STOP
                           and norm(x.text) not in ("nomme", "nommee", "appele", "appelee")]
                    if nxt and re.fullmatch(r"[A-Za-zÀ-ÿ_0-9]+", nxt[0].text):
                        name = nxt[0].text
                    break
        if not name:
            raise ValueError("Nom introuvable : « ajoute un assuré particulier nommé Karim de 40 ans »")
        uri = re.sub(r"[^A-Za-z0-9_]", "", unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode())
        uri = uri[:1].upper() + uri[1:]
        if uri in self.itypes:
            raise ValueError(f"« {uri} » existe déjà dans l'ontologie.")
        pt = plain(text).replace(plain(name), " ")
        if cls == "Assure" and re.search(r"\d+\s*ans?\b", pt):
            cls = "AssureParticulier"                  # l'âge n'existe que pour les particuliers
        ok = lambda p: p in self.dprops and self.compatible(self.pdom.get(p, ""), cls)
        info["classe"] = cls
        info["verif"] = self._verif(uri)
        tr = [f"ns:{uri} rdf:type ns:{cls}", f"ns:{uri} rdf:type owl:NamedIndividual"]
        np_ = self._default(NAME_PROP, cls)
        if np_:
            tr.append(f"ns:{uri} ns:{np_} {self._lit(np_, name.replace('_', ' '))}")
        done = set()

        def add(p, v):
            if ok(p) and p not in done:
                tr.append(f"ns:{uri} ns:{p} {self._lit(p, v)}")
                done.add(p)

        mm = re.search(r"(\d+)\s*ans?\b", pt)
        if mm: add("age", mm.group(1))
        mm = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|metres? carres?)", pt)
        if mm: add("surfaceM2", mm.group(1))
        for cue, p in (("valeur", "valeurBien"), ("montant", self._default(DEFAULT_NUM, cls)), ("plafond", "plafondGarantie"),
                       ("plafond", "plafondIndemnisation"), ("franchise", "franchise"), ("gravite", "niveauGravite"),
                       ("niveau", "niveauGravite"), ("taux", "tauxIndemnisation"), ("surface", "surfaceM2"),
                       ("prime", "montantPrime")):
            mm = re.search(rf"\b{cue}\w*\s*(?:de\s+|d'|=|:|est\s+)?\s*(\d+(?:[.,]\d+)?)", pt)
            if mm and p: add(p, mm.group(1))
        mm = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:tnd|dinars?|dt)\b", pt)
        if mm and self._default(DEFAULT_NUM, cls): add(self._default(DEFAULT_NUM, cls), mm.group(1))
        mm = re.search(r"(\d{4}-\d{2}-\d{2})", pt)
        if mm and self._default(DEFAULT_DATE, cls): add(self._default(DEFAULT_DATE, cls), mm.group(1))
        mm = re.search(r"(\d{1,3}\s*tun\s*\d{1,4})", pt)
        if mm: add("immatriculation", mm.group(1).upper())
        mm = re.search(r"\b(annuelle|mensuelle|trimestrielle|semestrielle)\b", pt)
        if mm: add("periodicite", mm.group(1).capitalize())
        mm = re.search(r"numero\s*(?:de contrat\s*)?(?:=|:)?\s*([a-z0-9-]+)", pt)
        if mm: add("numeroContrat", mm.group(1).upper())
        # liens vers les individus cités (propriété objet dont domaine/codomaine conviennent)
        for ind in info["individus"]:
            for d, r, p in self.oprops:
                if d in self.supers(cls) and r in self.itypes[ind]:
                    tr.append(f"ns:{uri} ns:{p} ns:{ind}")
                    break
        return f"{PREFIXES}INSERT DATA {{\n  " + " .\n  ".join(tr) + " .\n}"

    # ------------------------------------------------------------------ DELETE
    def _delete(self, text, info, doc):
        if not info["individus"]:
            raise ValueError("Individu inconnu : « supprime Karim »")
        i = info["individus"][0]
        return (f"{PREFIXES}DELETE {{ ns:{i} ?p ?o . ?s ?q ns:{i} }}\n"
                f"WHERE {{ {{ ns:{i} ?p ?o }} UNION {{ ?s ?q ns:{i} }} }}")

    # ------------------------------------------------------------------ UPDATE
    def _update(self, text, info, doc):
        p = next((x for x in info["proprietes"] if x in self.dprops), None)
        if not (info["individus"] and p):
            raise ValueError("Écris par ex. « modifie l'âge de Ahmed à 36 »")
        i = info["individus"][0]
        info["verif"] = self._verif(i)
        m = re.search(r"(?:^|\s)(?:à|a|en|par)\s+(.+?)\s*(?:ans|tnd|dinars?|dt|m2|%)?\s*$", text.strip(), re.I)
        if not m:
            raise ValueError("Valeur introuvable : termine par « à <valeur> »")
        v = m.group(1)
        lit = self._lit(p, v if self.dprops[p] != "string" else v[:1].upper() + v[1:])
        return (f"{PREFIXES}DELETE {{ ns:{i} ns:{p} ?old }}\nINSERT {{ ns:{i} ns:{p} {lit} }}\n"
                f"WHERE {{ OPTIONAL {{ ns:{i} ns:{p} ?old }} }}")
