# BDR demo voice 2 transcript

[000.43 - 006.99] Hey, I'm Manuel, and this is the BDR pipeline I built as a proof artifact for AI-assisted
[006.99 - 008.83] go-to-market workflows.
[008.83 - 011.19] The idea is simple.
[011.19 - 016.19] Take a target company, enrich it with public signals, choose a relevant outreach angle,
[016.19 - 022.67] generate a multi-touch sequence and run a quality critique before a human reviews anything.
[022.67 - 024.63] I want to be clear about the claim.
[024.63 - 026.51] This is not a revenue case study.
[026.51 - 031.95] I do not have campaign metrics or permission to show private client data, so this public
[031.95 - 035.55] version uses anonymized demo companies.
[035.55 - 041.43] The honest claim is, I built a multi-agent video workflow that turns a target company
[041.43 - 048.39] into a researched outreach using live signals, tenant-specific positioning, sequence generation
[048.39 - 050.31] and a critic pass.
[050.31 - 054.87] The problem is that outbound prep is just straight up slow.
[054.87 - 059.55] For each account, someone has a research company, understand what is happening to the business.
[059.55 - 065.03] Find the right persona, choose an angle, write the first email, write follow-ups and check
[065.03 - 066.67] whether it sounds human.
[066.67 - 071.27] That can easily take up to 20 to 30 minutes per account.
[071.27 - 077.03] So I build a repeatable workflow instead of a one-off prompt.
[077.03 - 082.71] The app uses streamlets for the interface and land graph for orchestration.
[082.71 - 084.55] The first stage is enrichment.
[084.55 - 092.79] The system pulls public company signals with EXR and uses hot.io for contact information
[092.79 - 101.63] discovery when obviously real data exists and uses Claude to summarize the accounts through
[101.63 - 104.43] configured ICP and persona.
[104.43 - 111.07] Next, the strategist chooses one of the three tenant-defined outreach angles that matters
[111.07 - 116.55] because good outbound is not just better wording, it's choosing the right reason to reach
[116.55 - 117.55] out.
[117.55 - 121.67] Then, the humanizer creates an outreach package.
[121.67 - 130.55] The model generates specific observations, but proof points, CTAs, subject lines and follow-up
[130.55 - 134.27] structures come from tenant-copy banks.
[134.27 - 138.15] That keeps the output more consistent and less generic.
[138.15 - 144.79] The pipeline then creates a sequence, LinkedIn Connect, first email, follow-up, social proof,
[144.79 - 147.23] LinkedIn DM and break up email.
[147.23 - 155.03] Finally, the critic scores each touch on pain specificity, proof relevance, CTA clarity
[155.03 - 156.71] and human voice.
[156.71 - 166.31] The output is not autosent, it's a research first draft for human to review and edit.
[166.31 - 171.95] All the public demo are using fictional companies, so contact discovery is not being found
[171.95 - 182.03] here, but it will be found when using real companies as X, as hunter.io is very reliable.
[182.03 - 188.83] The workflow works quite well, but this is just the first draft and the things I would like
[188.83 - 197.59] to improve are reply tracking, CRM export, campaign analytics, better ICP scoring and an
[197.59 - 205.15] EVA system which learns from previous runs to improve each run after each other.
[205.15 - 212.51] But now this workflow just shows at a low level and a service level how AI workflows can
[212.51 - 214.15] improve real business problems.