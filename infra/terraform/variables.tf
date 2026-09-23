variable "region" {
  description = "Region AWS. Paris par defaut : les donnees sont francaises et publiques, mais la latence et le cout y sont bons."
  type        = string
  default     = "eu-west-3"
}

variable "nom_seau" {
  description = "Prefixe du nom du seau S3. L'identifiant du compte y est ajoute, un nom de seau etant global."
  type        = string
  default     = "radar-entreprises-herault"
}

variable "prefixe" {
  description = "Prefixe des objets a l'interieur du seau. C'est aussi le perimetre exact des droits IAM."
  type        = string
  default     = "radar"
}

variable "base_catalogue" {
  description = "Nom de la base du catalogue Glue interrogee par Athena."
  type        = string
  default     = "radar_entreprises"
}

variable "workgroup" {
  description = "Nom du workgroup Athena du projet."
  type        = string
  default     = "radar"
}

variable "octets_max_par_requete" {
  description = "Garde-fou de cout Athena : une requete au-dela de cette taille balayee echoue. 1 Go par defaut, le jeu de l'Herault en pese moins de 20 Mo par an."
  type        = number
  default     = 1073741824
}

variable "autoriser_destruction" {
  description = "Autorise terraform destroy a supprimer un seau non vide. A laisser a false hors bac a sable."
  type        = bool
  default     = false
}
