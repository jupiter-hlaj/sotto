# Sotto Custom Domains — Runbook & Reference

This doc captures how `sotto.cloud` is wired end-to-end: domain registration,
DNS hosting, TLS certificates, and the SAM / GitHub Actions plumbing that
connects the friendly URLs (`api-dev.sotto.cloud`, `portal-dev.sotto.cloud`,
etc.) to the underlying AWS resources. Read this if you need to add a new
subdomain, rotate the certificate, change environments, or debug a DNS issue.

Last updated: 2026-04-10 (M1.5 of Teams Phone integration work).

---

## 1. The big picture

```
  Porkbun (registrar + DNS host)
         │
         │   CNAME records:
         │   _bb0102... → _6b39be...acm-validations.aws.   (ACM cert validation)
         │   api-dev    → <API Gateway regional domain>
         │   portal-dev → <CloudFront distribution domain>
         │   api        → <API Gateway regional domain>   (prod)
         │   portal     → <CloudFront distribution domain> (prod)
         ▼
  AWS (us-east-1)
    ├─ ACM certificate  *.sotto.cloud + sotto.cloud   (single wildcard, both envs share it)
    ├─ sotto-dev stack   → API custom domain api-dev.sotto.cloud, CloudFront alias portal-dev.sotto.cloud
    └─ sotto-prod stack  → API custom domain api.sotto.cloud,     CloudFront alias portal.sotto.cloud
```

**Key decisions, locked in:**
- **Registrar:** Porkbun. No plan to move.
- **DNS host:** Porkbun's own built-in DNS. Not Route 53, not Cloudflare.
- **Certificate:** one wildcard ACM cert covering `*.sotto.cloud` + `sotto.cloud`, living in `us-east-1` (required because CloudFront only reads certs from us-east-1). Both dev and prod stacks reference the same cert ARN — there is no reason to have two.
- **Subdomain naming:** dash-separated dev subdomains. `api-dev.sotto.cloud`, `portal-dev.sotto.cloud`, `bots-dev.sotto.cloud` for dev. `api.sotto.cloud`, `portal.sotto.cloud`, `bots.sotto.cloud` for prod.

---

## 2. The ACM certificate

**ARN (referenced from samconfig.toml and both GitHub Actions workflows):**
```
arn:aws:acm:us-east-1:821891894512:certificate/16659c98-f201-4826-8761-c298c73069b2
```

**Covers:** `*.sotto.cloud` (primary) and `sotto.cloud` (SAN, for the bare apex if we ever need it).

**Issued:** 2026-04-10.  **Expires:** 2026-10-24. After that ACM renews it automatically every ~13 months, as long as the validation CNAME (see below) is still present at Porkbun.

### The validation CNAME — DO NOT DELETE
One CNAME record at Porkbun proves to AWS that we own the domain. ACM re-reads it at every renewal. If you delete it, the next auto-renew will silently fail and eventually the cert will expire.

| Field | Value |
|---|---|
| Type | `CNAME` |
| Host | `_bb0102c52baac36da7f6181ec198d1bf` |
| Answer | `_6b39beb984d49c32b761ea8f951eeda8.jkddzztszm.acm-validations.aws.` |

### If you need to request a NEW cert (replacement or additional domains)
```bash
aws acm request-certificate \
  --domain-name "*.sotto.cloud" \
  --subject-alternative-names "sotto.cloud" \
  --validation-method DNS \
  --key-algorithm RSA_2048 \
  --region us-east-1 \
  --tags Key=Project,Value=sotto Key=Purpose,Value=wildcard-tls

aws acm describe-certificate \
  --certificate-arn <new-arn> \
  --region us-east-1 \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```
Then add the returned CNAME at Porkbun and wait ~5–30 minutes for AWS to flip the cert from `PENDING_VALIDATION` → `ISSUED`.

---

## 3. SAM parameters (backend/template.yaml)

Three parameters control custom-domain wiring. If ALL are empty (default), the
custom-domain logic is skipped entirely and the stack falls back to the default
`*.execute-api.amazonaws.com` and `*.cloudfront.net` URLs. If `WildcardCertArn`
is set, the `HasCustomDomains` condition flips on and the extra resources get
created.

| Parameter | Dev value | Prod value |
|---|---|---|
| `WildcardCertArn` | `arn:aws:acm:us-east-1:821891894512:certificate/16659c98-f201-4826-8761-c298c73069b2` | (same ARN) |
| `ApiDomain` | `api-dev.sotto.cloud` | `api.sotto.cloud` |
| `PortalDomain` | `portal-dev.sotto.cloud` | `portal.sotto.cloud` |

These are declared in two places and **must stay in sync**:

1. **`backend/samconfig.toml`** — used only when someone runs `sam deploy` from their laptop (rare; mostly for local debugging).
2. **`.github/workflows/deploy-dev.yml`** and **`.github/workflows/deploy-prod.yml`** — used by CI (the normal path). CI does NOT read samconfig.toml; it passes parameters inline via `--parameter-overrides`.

> **Gotcha:** if you add a new parameter and forget to update the workflow files, CI will deploy with an empty/default value and you'll wonder why nothing happened. Every workflow's `SAM Deploy` step has a comment pointing at this doc.

### What gets created when `HasCustomDomains` is true
- `ApiCustomDomain` — `AWS::ApiGatewayV2::DomainName` for the HTTP API, bound to the ACM cert.
- `ApiCustomDomainMapping` — maps `ApiCustomDomain` → `SottoHttpApi` stage.
- `PortalDistribution.Aliases` — adds `PortalDomain` to the CloudFront distribution.
- `PortalDistribution.ViewerCertificate` — tells CloudFront to serve TLS using the ACM cert (`TLSv1.2_2021`, `sni-only`).
- Two new stack outputs: `ApiCustomDomainTarget` and `PortalCustomDomainTarget` — these are the "where do I point the CNAME" values you need at Porkbun.

---

## 4. Deployment flow (what actually happens)

### Dev
1. Push to `main` branch → `.github/workflows/deploy-dev.yml` runs automatically.
2. Workflow builds SAM, deploys `sotto-dev` stack, runs a `/health` smoke test, syncs the admin portal to S3, invalidates CloudFront.
3. Grab the stack outputs to find the Porkbun CNAME targets:
   ```bash
   aws cloudformation describe-stacks --stack-name sotto-dev \
     --query "Stacks[0].Outputs[?OutputKey=='ApiCustomDomainTarget'||OutputKey=='PortalCustomDomainTarget']" \
     --output table
   ```

### Prod
1. Tag a release (`git tag v1.2.3 && git push --tags`) → `.github/workflows/deploy-prod.yml` runs.
2. Requires manual approval via the GitHub `prod` environment.
3. Canary monitoring + rollback logic is already in the workflow.

### Local (rare — only for debugging)
```bash
cd backend
sam build
sam deploy --config-env dev     # reads samconfig.toml
```

---

## 5. Porkbun DNS records inventory

Kept here as the source of truth so you don't have to log into Porkbun to see what's configured. Update this table whenever you add or change a record.

| Host | Type | Target | Purpose |
|---|---|---|---|
| `_bb0102c52baac36da7f6181ec198d1bf` | CNAME | `_6b39beb984d49c32b761ea8f951eeda8.jkddzztszm.acm-validations.aws.` | ACM cert validation (DO NOT DELETE) |
| `api-dev` | CNAME | *(TBD — populate from `ApiCustomDomainTarget` after first dev deploy)* | HTTP API (dev) |
| `portal-dev` | CNAME | *(TBD — populate from `PortalCustomDomainTarget` after first dev deploy)* | Admin portal (dev) |
| `api` | CNAME | *(TBD — populate after first prod deploy)* | HTTP API (prod) |
| `portal` | CNAME | *(TBD — populate after first prod deploy)* | Admin portal (prod) |
| `bots-dev` | *(TBD — added in T-4/T-5, Teams bot)* | — | Teams bot ingress (dev) |
| `bots` | *(TBD — added in T-4/T-5, Teams bot)* | — | Teams bot ingress (prod) |

### Porkbun-specific gotchas
- **"Host" field does NOT include `.sotto.cloud`** — Porkbun appends that automatically. Just enter the subdomain label.
- Trailing dots in the Answer field: Porkbun sometimes accepts and sometimes strips them. Either way works.
- DNS propagation is usually fast (a few minutes) but can take up to ~30 minutes for first-time records.

---

## 6. Adding a new subdomain (e.g. a future `docs.sotto.cloud`)

1. **Certificate:** nothing to do — the existing wildcard cert already covers it.
2. **Point it at a target:** figure out where it should go (S3 static site? another CloudFront? a Lambda function URL?) and get the target DNS name.
3. **Add a CNAME at Porkbun:** Host = `docs`, Answer = target DNS name.
4. **Update this doc's DNS inventory table** so the next person can see it exists.

If the new subdomain is served by a new CloudFront distribution or API Gateway that you're adding to `template.yaml`, reference `!Ref WildcardCertArn` on the distribution's `ViewerCertificate` / API's `DomainNameConfigurations.CertificateArn` — same cert, no extra cost, no new validation step.

---

## 7. Troubleshooting

**"Why is my browser showing a cert warning for `api-dev.sotto.cloud`?"**
- Check that `WildcardCertArn` is actually being passed to CI. `grep WildcardCertArn .github/workflows/deploy-dev.yml` should show it.
- Check the stack was deployed with `HasCustomDomains=true`:
  `aws cloudformation describe-stacks --stack-name sotto-dev --query "Stacks[0].Parameters"`.
- Confirm the Porkbun CNAME points at the right CloudFront domain (`PortalCustomDomainTarget` output).

**"CloudFront deploy is taking forever"**
- Normal. CloudFront edge config updates are 5–15 minutes for any change that touches the distribution (aliases, certs, behaviors). The sam deploy step will just sit there spinning — it's not stuck.

**"ACM cert auto-renewal failed"**
- Verify the validation CNAME is still present in Porkbun (section 2 above).
- Check ACM events: `aws acm describe-certificate --certificate-arn <arn> --region us-east-1 --query 'Certificate.RenewalSummary'`.

**"I deleted the validation CNAME by accident"**
- Re-add it using the values from section 2. AWS will re-validate on the next auto-renewal cycle — no immediate action needed unless the cert is actually about to expire.

---

## 8. Related specs
- `sotto-01-infrastructure.md` — general AWS infra spec (does not yet cover custom domains; predates M1.5).
- `sotto-teams-phone-integration.md` — Teams integration spec. The Azure AD app's OAuth redirect URL will need to point at a `sotto.cloud` subdomain once we finish M2. `bots.sotto.cloud` / `bots-dev.sotto.cloud` are reserved here for T-4/T-5 (Teams bot ECS service).
