.. _engine vin:

==========
VIN decoder
==========

.. sidebar:: info

   - :origin:`vin.py <searx/engines/vin.py>`
   - :ref:`offline engines`

The *VIN* engine decodes a vehicle identification number (VIN) **locally** -- no
request is sent to the internet.  As soon as a 17 character VIN is found in the
query, the engine returns an answer with the decoded content of the VIN.

Two VIN layouts are known and both are supported:

**European layout** (most vehicles sold in the EU)

===== ==========================================================
Pos.  Content
===== ==========================================================
1-3   WMI (manufacturer, country, division)
4-9   vehicle *type* (Mercedes: ``Baureihe.Nachnummer``, e.g.
      ``156912`` -> 156.912 -> GLA 180 d)
10    1 = left hand drive, 2 = right hand drive
11    plant / factory code
12-17 production serial number
===== ==========================================================

**North American layout** (USA/Canada, 49 CFR 565)

===== ==========================================================
Pos.  Content
===== ==========================================================
1-3   WMI
4-5   series (e.g. ``DA`` -> W166 M-Class/GLE-Class)
6-7   model within the series (e.g. ``5J`` -> ML 350 4MATIC)
8     restraint system
9     check digit (mandatory in North America)
10    model year
11    plant
12-17 production serial number
===== ==========================================================

.. attention::

   The check digit (ISO 3779) is only mandatory for vehicles built for North
   America.  In a European VIN the 9th character is *not* a check digit -- a
   mismatch is **no** indication of a manipulated VIN.

Configuration
=============

.. code:: yaml

  - name: vin
    engine: vin
    shortcut: vin
    categories:
      - general
    disabled: false

Example
=======

.. code:: text

   WDC1569121J092219
   -> Mercedes-Benz GLA 180 d, 1.5 l turbo-diesel (OM607.951), 109 hp,
      front wheel drive, built in Sindelfingen (J), left hand drive,
      production number 092219

Implementation
==============

.. automodule:: searx.engines.vin
  :members:
