---
pip: 11
title: Soulbound Token (SBT) Issuance upon KYC Verification
proposer:  @Tetz on GitHub
status: Rejected
type: Token & Community Design
created: 2025-07-11
requires: []
replaces: []
---


# PIP-11: Soulbound Token (SBT) Issuance upon KYC Verification

## Required Information for Each PIP

* **Title**
  Soulbound Token (SBT) Issuance upon KYC Verification

* **PIP Number**
  11

* **Proposer**
  Tetsuro Takemoto

* **Submission Date**
  2025-07-11

* **Status**
  Draft

* **Category**
  Token & Community Design

* **Motivation**
  To enhance governance integrity and security within the PEACE COIN DAO by issuing non-transferable Soulbound Tokens (SBTs) to users who complete Know Your Customer (KYC) verification. This system prevents Sybil attacks, establishes verified identity in a privacy-preserving manner, and enables advanced voting schemes based on trust and accountability.

* **Specification / Details**

  * Upon successful KYC verification, the user will receive a unique, non-transferable SBT.
  * The SBT acts as an on-chain proof of identity verification while maintaining privacy (only the verified status is public, not personal data).
  * This token may be used for:

    * Weighted governance participation
    * Access to private proposals or delegate privileges
    * Enhanced trust scoring and SBT-based evaluation mechanisms
  * Evaluation of third-party KYC providers is ongoing. Priority is given to those supporting Japanese documents like driver's licenses and offering on-chain verifiability:

    * **Fractal ID**: Built-in SBT issuance and Japanese ID support (confirmed)
    * **Persona**: Custom workflow with flexible backend integration
    * **Civic**: KYC + SBT, privacy-focused, but needs implementation evaluation
  * The SBT standard will follow ERC-721 or ERC-4973 guidelines.
  * A revocation mechanism may be introduced via future PIP.

* **Contact**
  * GitHub: [github.com/Tetz](https://github.com/Tetz)


* **Notes (optional)**:

  * **The need for SBT-based evaluation**:

    * Adds a layer of verified identity for weighted governance
    * Enhances the DAO's resistance to Sybil attacks
  * **Scope of impact and potential risks**:

    * May exclude anonymous participants from some governance areas
    * Dependency on third-party services and risk of data breach
  * **Alternatives and rationale**:

    * Anonymous-only governance (lower trust, higher attack vector)
    * Off-chain verification (not transparent or composable)
  * **Start date and duration of the Signal Vote**:

    * To be scheduled no earlier than 48 hours post-Draft publication
    * Voting Period: 72 hours
  * **Last Call Deadline**: TBD
  * **Requires**: PIP-1 (PEACE COIN Governance Framework)

* **Editor**
  TBD (assigned during review phase)
