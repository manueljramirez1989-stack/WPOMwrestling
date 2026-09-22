WPOM landing page + QR (no event branding, reusable at every tournament)

1. Supabase SQL editor: run landing_setup.sql once.
2. GitHub repo (same repo as index.html), keep folder names exactly:
      go/index.html   landing page              -> wpomwrestling.com/go
      Q/index.html    tiny redirect for the QR  -> wpomwrestling.com/Q  ->  /go/?s=shirt
3. Shirt (BLACK shirt, two ink colours: gold #C8A84B and cream #F4EFE6 - the same palette as the WPOM graphics):
   shirt_back.svg               12 x 15.6 in. The black in the file is the shirt itself. The QR box prints cream; the QR squares are
                                NOT printed - they are the black shirt showing through (knock-out). Keep the cream margin around the code.
   shirt_front_left_chest.svg   4 in hex logo, gold outline, cream lettering.
   QR alone: wpom_qr.svg / wpom_qr.png. Keep the white margin. Scan-test a paper printout at full size before ordering.
4. Other links: wpomwrestling.com/go/?s=instagram , ?s=email , ?s=flyer  - each is counted separately.
5. Numbers:  select * from landing_funnel;
