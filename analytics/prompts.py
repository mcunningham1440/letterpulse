"""
Prompt templates for LLM calls used throughout the analytics app.
"""

# Used in extract_sections() — system message for multi-section extraction.
SECTION_PARSING_PROMPT = """You are given an HTML document with line numbers.
Your task is to identify items within the HTML that belong to each of the named sections listed at the bottom.
Each section has a name and a description of the content to look for.

For each section, determine first if it exists in the newsletter.
If it does, then determine if it represents a single story/essay/concept/ad, etc., 
or if it consists of a collection of distinct items, such as a bulleted list of links to news articles about different events.
If it is the former, provide the start and end HTML line numbers (inclusive) for the section.
If it is the latter, provide the start and end line numbers (inclusive) for each of the distinct items within the section.
Make each item a separate pair of start and end line numbers.

Example1 shows a section consisting of a single item--in this case, an ad.

<Example1>
748	                <tr>
749	                 <td align="left" class="dd" id="chicago-in-context-hackathon-on-sat" style="color:#2A2A2A;font-weight:400;padding:0px 25px;text-align:left;" valign="top">
750	                  <h1 style="color:#2A2A2A;font-weight:400;mso-line-height-alt:175.0%;">
751	                   Chicago in Context Hackathon on Saturday
752	                  </h1>
753	                 </td>
754	                </tr>
755	                <tr>
756	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
757	                  <p style="mso-line-height-alt:150.0%;">
758	                   This Saturday, we’re bringing AI engineers together for a hands-on hackathon exploring Model Context Protocol (MCP) which is a new open standard that lets AI securely access and act on real-world data.
759	                  </p>
760	                 </td>
761	                </tr>
762	                <tr>
763	                 <td align="center" class="dd" style="padding-bottom:20px;padding-left:25px;padding-right:25px;padding-top:40px; " valign="top">
764	                  <table border="0" cellpadding="0" cellspacing="0" role="none" style="margin:0 auto 0 auto;">
765	                   <tr>
766	                    <td align="center" style="width:620px;" valign="top">
767	                     <img alt="" border="0" height="auto" src="https://media.beehiiv.com/cdn-cgi/image/fit=scale-down,format=auto,onerror=redirect,quality=80/uploads/asset/file/2473d619-2756-4b5c-b6cd-fd9fda6c7aaa/Screenshot_2025-10-09_at_4.23.11_PM.png?t=1760044998" style="display:block;width:100%;" width="620"/>
768	                    </td>
769	                   </tr>
770	                  </table>
771	                 </td>
772	                </tr>
773	                <tr>
774	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
775	                  <p style="mso-line-height-alt:150.0%;">
776	                   We’ll build agents that use Chicago’s open source municipal data to create real solutions for Chicagoans. This is perfect for anyone excited about AI, urban systems, and open data:
777	                  </p>
778	                 </td>
779	                </tr>
780	                <tr class="embed-gen-img-r">
781	                 <td align="center" class="dd" style="padding:12px 37px 12px 37px;" valign="top">
782	                  <table align="center" border="0" cellpadding="0" cellspacing="0" role="none" width="100%">
783	                   <tr>
784	                    <td align="center" class="o" style="padding:12px 12px 12px 12px;;background-color:#FFFFFF;border-color:#2C81E5;border-radius:5px 5px 5px 5px;border-width:1px 1px 1px 1px;" valign="top">
785	                     <!--[if !mso]><!-->
786	                     <div class="mob-show" style="display:none; float:left; overflow:hidden; width:0; max-height:0; line-height:0;">
787	                      <table align="right" border="0" cellpadding="0" cellspacing="0" role="none" width="100%">
788	                       <tr>
789	                        <td align="center" valign="top">
790	                         <a href="https://link.mail.beehiiv.com/ss/c/u001.5SCiSvLPLPy8TzmqrMQK1H7oyvsBadZaPdrDn6ppk63d7rffWrXwI-u3aZiE10H9-OYABaN8vM5zo8Nojj8kUW9Or7xHwaFPv6mZI8SAXd0bkb9RMuStjK639nwMUgMFYt54OoP4uUxxX_c42tikcSB4CR_r7h90O5pj5euet87A3r8tRQPtR3tix7dhG5zid-Cdk5S2ezuzgrfmIX7tDDU-vHOwGyLjZ_D-Rr0YXHDBvNEBzC3wQzlltCBxigrlaY69kWr4_Nh_E5n5PsEfso4Kp2rYpainZW33qGGrMek_o-r4yrKwuUAahLPJB5bK/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h9/h001.fqolzqCo2IsArJHjfJ7-SR2qQUBiTm2VwufOxTElaxc" target="_blank">
791	                          <img src="https://sloppy-joe-app.imgix.net/integrated_text_banner/_wIG8dBmvx90.jpg?fm=jpg" style="height:auto;display:block;" width="100%"/>
792	                         </a>
793	                        </td>
794	                       </tr>
795	                       <tr>
796	                        <td height="16" style="font-size:16px;line-height:16px;">
797	                        </td>
798	                       </tr>
799	                      </table>
800	                     </div>
801	                     <!--<![endif]-->
802	                     <table align="right" border="0" cellpadding="0" cellspacing="0" role="none" width="100%">
803	                      <tr>
804	                       <td align="center" class="mob-stack" valign="middle" width="57%">
805	                        <table align="center" border="0" cellpadding="0" cellspacing="0" role="none" width="100%">
806	                         <tr>
807	                          <td align="left" class="l" valign="middle">
808	                           <p>
809	                            <a href="https://link.mail.beehiiv.com/ss/c/u001.5SCiSvLPLPy8TzmqrMQK1H7oyvsBadZaPdrDn6ppk63d7rffWrXwI-u3aZiE10H9-OYABaN8vM5zo8Nojj8kUW9Or7xHwaFPv6mZI8SAXd0bkb9RMuStjK639nwMUgMFYt54OoP4uUxxX_c42tikcSB4CR_r7h90O5pj5euet87A3r8tRQPtR3tix7dhG5zid-Cdk5S2ezuzgrfmIX7tDDU-vHOwGyLjZ_D-Rr0YXHDBvNEBzC3wQzlltCBxigrlpXRiSObEEA9gjpprPnu15LxtFgwqP54IsnU_RW5vjC5KrhUxVr3tqLaea8GeMtaP/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h10/h001.YFBfO6tdq73PytUkKC49HE68ADVKskQtoDc1lFoWlPg" style="text-decoration:none;font-style:normal;color:#2D2D2D !important;font-size:14px;line-height:20px;" target="_blank">
810	                             Chicago in Context: MCP Mini-Hack ft. LiquidMetal [AI Tinkerers - Chicago]
811	                             <tr>
812	                              <td align="left" class="m" valign="top">
813	                               <p style="font-size:13px;line-height:19px;color:#2D2D2D;">
814	                                Join us on Saturday November 1st from 12–5:30 PM at Drive Capital for the Chicago in Context: MCP Mini-Hack
815	                               </p>
816	                              </td>
817	                             </tr>
818	                             <tr>
819	                              <td align="left" class="n" style="vertical-align:bottom;padding-top:12px;" valign="bottom">
820	                               <p style="word-break:break-word;">
821	                                chicago.aitinkerers.org/p/chicago-in-context-mcp-mini-hack-ft-liquidmetal
822	                               </p>
823	                              </td>
824	                             </tr>
825	                            </a>
826	                           </p>
827	                          </td>
828	                         </tr>
829	                        </table>
830	                       </td>
831	                       <td class="mob-hide" style="font-size:16px;line-height:16px;" width="3%">
832	                       </td>
833	                       <td align="left" class="mob-hide" valign="top" width="40%">
834	                        <a href="https://link.mail.beehiiv.com/ss/c/u001.5SCiSvLPLPy8TzmqrMQK1H7oyvsBadZaPdrDn6ppk63d7rffWrXwI-u3aZiE10H9-OYABaN8vM5zo8Nojj8kUW9Or7xHwaFPv6mZI8SAXd0bkb9RMuStjK639nwMUgMFYt54OoP4uUxxX_c42tikcSB4CR_r7h90O5pj5euet87A3r8tRQPtR3tix7dhG5zid-Cdk5S2ezuzgrfmIX7tDDU-vHOwGyLjZ_D-Rr0YXHDBvNEBzC3wQzlltCBxigrlQw9AD9C8ZsUBHlSrlLWk5VD_O5mIwY59VWKSzwrJ7QJLmtWxZmSp8A1CWkzRrXAC/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h11/h001.v6lg3nLTr9LnGgAlFyGSufxqfLeb-0zDnWV-DuelFsE" target="_blank">
835	                         <img src="https://sloppy-joe-app.imgix.net/integrated_text_banner/_wIG8dBmvx90.jpg?fm=jpg" style="height:auto;display:block;" width="228"/>
836	                        </a>
837	                       </td>
838	                      </tr>
839	                     </table>
840	                    </td>
841	                   </tr>
842	                  </table>
843	                 </td>
844	                </tr>
845	                <tr>
846	                 <td align="center" class="dd" style="font-size:0px;line-height:0px;padding:20px 0px 20px;" valign="top">
847	                  <table align="center" border="0" cellpadding="0" cellspacing="0" class="j" role="none" width="50%">
848	                   <tr>
849	                    <td>
850	                    </td>
851	                   </tr>
852	                  </table>
853	                 </td>
854	                </tr>
855	                <tr>
856	                 <td align="left" class="dd" id="the-ai-insights-every-decision-make" style="color:#2A2A2A;font-weight:normal;padding:0px 25px;text-align:left;" valign="top">
857	                  <h3 style="color:#2A2A2A;font-weight:normal;mso-line-height-alt:125.0%;">
858	                   The AI Insights Every Decision Maker Needs
859	                  </h3>
860	                 </td>
861	                </tr>
862	                <tr>
863	                 <td align="center" class="dd" style="padding-bottom:20px;padding-left:25px;padding-right:25px;padding-top:40px; " valign="top">
864	                  <table border="0" cellpadding="0" cellspacing="0" role="none" style="margin:0 auto 0 auto;">
865	                   <tr>
866	                    <td align="center" style="width:620px;" valign="top">
867	                     <a href="https://link.mail.beehiiv.com/ss/c/u001.yh18HaJ_gjAQAppHY8AEokNJY7dUvobuKVUxD0ALr3Zhz7WxO3xdGM2DDpT7VrMN_HTGSCqirD7Y2VQTd098uL38h6vlGww8-LctZO8GYeRthoeCpzbfWXPhppA4ArnOHDkD2MSG7Q_b8EbideRx3ObDz1ben_T3O4qSq3M-Ku6BOWp03IWUbxfdusZ6LPltjq0K0w3wn5So9Ugbl_dfe2TL44VQx1KtP2ejRpNEHHJipf6NadVE6vogRDWj6_bDFo3kp_rCElaLtY6bdnxXq64TQFCBNftkHJkUYQoMc5N61FZzQwMFQ1OlUppz-Hq420M297aSZ_9RQQx4fwsC96-kPfrDwwDVI-pMO0gRJh5WSh8jUlu0rjWM9KotM8z_02mwaHj6IRdFgEG48EMXl1SHWPuCmVvLiEQn2KpR9Y74WtvHKun4pnq-zIahQc6a/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h12/h001.W4cVwFiLTNYghDL6Oa-dMMa4GQ-YPdgaSsmMfFLKOos" rel="noopener noreferrer nofollow" style="text-decoration:none;" target="_blank">
868	                      <img alt="" border="0" height="auto" src="https://media.beehiiv.com/cdn-cgi/image/fit=scale-down,format=auto,onerror=redirect,quality=80/uploads/asset/file/e3f15559-3acc-4182-a65b-aa5530e569b2/CB_5.png?t=1757641075" style="display:block;width:100%;" width="620"/>
869	                     </a>
870	                    </td>
871	                   </tr>
872	                  </table>
873	                 </td>
874	                </tr>
875	                <tr>
876	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
877	                  <p style="mso-line-height-alt:150.0%;">
878	                   You control budgets, manage pipelines, and make decisions, but you still have trouble keeping up with everything going on in AI. If that sounds like you, don’t worry, you’re not alone – and
879	                   <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.yh18HaJ_gjAQAppHY8AEokNJY7dUvobuKVUxD0ALr3Zhz7WxO3xdGM2DDpT7VrMN_HTGSCqirD7Y2VQTd098uL38h6vlGww8-LctZO8GYeRthoeCpzbfWXPhppA4ArnOHDkD2MSG7Q_b8EbideRx3ObDz1ben_T3O4qSq3M-Ku6BOWp03IWUbxfdusZ6LPltjq0K0w3wn5So9Ugbl_dfe2TL44VQx1KtP2ejRpNEHHJipf6NadVE6vogRDWj6_bDFo3kp_rCElaLtY6bdnxXq64TQFCBNftkHJkUYQoMc5N61FZzQwMFQ1OlUppz-Hq420M297aSZ_9RQQx4fwsC96-kPfrDwwDVI-pMO0gRJh5WSh8jUlu0rjWM9KotM8z_QDgz-vZJaMwfn9sjG8XJLTu8MzMcBsEhyFDNdjq53h9_FIalVnF8xFwqybQH7ueK/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h13/h001.5SviFodluifs5DzY9pqVq14Ei-PRQqr3sAQXZoC4TYc" rel="noopener noreferrer nofollow" target="_blank">
880	                    <span>
881	                     The Deep View
882	                    </span>
883	                   </a>
884	                   is here to help.
885	                  </p>
886	                 </td>
887	                </tr>
888	                <tr>
889	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
890	                  <p style="mso-line-height-alt:150.0%;">
891	                   This
892	                   <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.yh18HaJ_gjAQAppHY8AEokNJY7dUvobuKVUxD0ALr3Zhz7WxO3xdGM2DDpT7VrMN_HTGSCqirD7Y2VQTd098uL38h6vlGww8-LctZO8GYeRthoeCpzbfWXPhppA4ArnOHDkD2MSG7Q_b8EbideRx3ObDz1ben_T3O4qSq3M-Ku6BOWp03IWUbxfdusZ6LPltjq0K0w3wn5So9Ugbl_dfe2TL44VQx1KtP2ejRpNEHHJipf6NadVE6vogRDWj6_bDFo3kp_rCElaLtY6bdnxXq64TQFCBNftkHJkUYQoMc5N61FZzQwMFQ1OlUppz-Hq420M297aSZ_9RQQx4fwsC96-kPfrDwwDVI-pMO0gRJh5WSh8jUlu0rjWM9KotM8z_GbRLPrftBtqnrUADH7TFAAN6Z-gYfodkm8ZpcvfMqUVEl6Dut6-Bm6V2yDglflBv/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h14/h001.7KIZa9otDMEBKk6iVa9ufMaPLux6WgrPJN_DMS2mKlY" rel="noopener noreferrer nofollow" target="_blank">
893	                    <span>
894	                     free, 5-minute-long daily newsletter covers everything you need to know about AI
895	                    </span>
896	                   </a>
897	                   . The biggest developments, the most pressing issues, and how companies from Google and Meta to the hottest startups are using it to reshape their businesses… it’s all broken down for you each and every morning into easy-to-digest snippets.
898	                  </p>
899	                 </td>
900	                </tr>
901	                <tr>
902	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
903	                  <p style="mso-line-height-alt:150.0%;">
904	                   If you want to up your AI knowledge and stay on the forefront of the industry,
905	                   <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.yh18HaJ_gjAQAppHY8AEokNJY7dUvobuKVUxD0ALr3Zhz7WxO3xdGM2DDpT7VrMN_HTGSCqirD7Y2VQTd098uL38h6vlGww8-LctZO8GYeRthoeCpzbfWXPhppA4ArnOHDkD2MSG7Q_b8EbideRx3ObDz1ben_T3O4qSq3M-Ku6BOWp03IWUbxfdusZ6LPltjq0K0w3wn5So9Ugbl_dfe2TL44VQx1KtP2ejRpNEHHJipf6NadVE6vogRDWj6_bDFo3kp_rCElaLtY6bdnxXq64TQFCBNftkHJkUYQoMc5N61FZzQwMFQ1OlUppz-Hq420M297aSZ_9RQQx4fwsC96-kPfrDwwDVI-pMO0gRJh5WSh8jUlu0rjWM9KotM8z_j-gntpAUL5IJN9Zc2LQwaRn3UKOr5WeTHYka3qmdgsGH0_iLIbteeKs5QMhbCXLF/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h15/h001.8kI1GJOio0EoYomq_TyPWr0ge-5VzSphxMHUSeybe-Q" rel="noopener noreferrer nofollow" target="_blank">
906	                    <span>
907	                     you can subscribe to The Deep View right here (it’s free!).
908	                    </span>
909	                   </a>
910	                  </p>
911	                 </td>
912	                </tr>
913	                <tr>
914	                 <td align="center" class="dd" style="font-size:0px;line-height:0px;padding:20px 0px 20px;" valign="top">
915	                  <table align="center" border="0" cellpadding="0" cellspacing="0" class="j" role="none" width="50%">
916	                   <tr>
917	                    <td>
918	                    </td>
919	                   </tr>
920	                  </table>
921	                 </td>
922	                </tr>
923	                <tr>
924	                 <td align="left" class="dd" id="this-weeks-chicago-tech-events" style="color:#2A2A2A;font-weight:400;padding:0px 25px;text-align:left;" valign="top">
925	                  <h1 style="color:#2A2A2A;font-weight:400;mso-line-height-alt:175.0%;">
926	                   📆
927	                   <b>
928	                    This Week’s Chicago Tech Events
929	                   </b>
930	                  </h1>
931	                 </td>
932	                </tr>
</Example1>

In this case, if the user asked for "Deep View ad", you would make it a single item, returning a single pair of start and end line numbers.
StartLine = 855, EndLine = 912

Example2 shows a section with multiple items.

<Example2>
923	                <tr>
924	                 <td align="left" class="dd" id="this-weeks-chicago-tech-events" style="color:#2A2A2A;font-weight:400;padding:0px 25px;text-align:left;" valign="top">
925	                  <h1 style="color:#2A2A2A;font-weight:400;mso-line-height-alt:175.0%;">
926	                   📆
927	                   <b>
928	                    This Week’s Chicago Tech Events
929	                   </b>
930	                  </h1>
931	                 </td>
932	                </tr>
933	                <tr>
934	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
935	                  <p style="mso-line-height-alt:150.0%;">
936	                   <b>
937	                    Navigate the Patient Landscape
938	                   </b>
939	                  </p>
940	                 </td>
941	                </tr>
942	                <tr>
943	                 <td class="ee" style="padding-bottom:12px;padding-left:47px;padding-right:37px;padding-top:12px;">
944	                  <div class="edm_outlooklist" style="margin-left:0px;">
945	                   <ul style="font-weight:normal;list-style-type:disc;margin-bottom:12px !important;margin-top:12px !important;padding:0px 0px 0px 0px;">
946	                    <li class="listItem ultext">
947	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
948	                      Tuesday
949	                     </p>
950	                    </li>
951	                    <li class="listItem ultext">
952	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
953	                      <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.E_InkxgnjWj38C_b_rz0E-MwqT_6dcKmqsZvLrYTR5EHACKq2hNMQcJ0wbnxTSVxYSNwSOHV2dDNh_CLToOij_524M4OuOoOi7_cEG9udEXNNM2a-9b1vJP_hMqAPiPGbm6MxSO-GvO4Q3G-sVM-dIn69HGRV6rfnlkOIDQHfVszL-cqAn8wVb4EsiQz9meRhvzB385T2QEoiiVFdQzO619Zd9itrK5TdnaRA2VLjxRfNuL8DQoZSvPXVVXDFT6jQgHlOTWq4jLGHYyUvw8WJqVMOZeJ0e6ePNe_g__Y0zIRd8yn9Ldj0-3ZtFwpyWPzPyWR322JUnt5BmS25aEXRgM7_7pv0oA5PKgVyVgyV64/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h16/h001.0-2hvKPdJr8NMcz_HLyxiRAJC4I6KNSZlHveg8lTxig" rel="noopener noreferrer nofollow" target="_blank">
954	                       <span>
955	                        RSVP
956	                       </span>
957	                      </a>
958	                     </p>
959	                    </li>
960	                   </ul>
961	                  </div>
962	                 </td>
963	                </tr>
964	                <tr>
965	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
966	                  <p style="mso-line-height-alt:150.0%;">
967	                   <b>
968	                    Connect &amp; Grow Chicago
969	                   </b>
970	                  </p>
971	                 </td>
972	                </tr>
973	                <tr>
974	                 <td class="ee" style="padding-bottom:12px;padding-left:47px;padding-right:37px;padding-top:12px;">
975	                  <div class="edm_outlooklist" style="margin-left:0px;">
976	                   <ul style="font-weight:normal;list-style-type:disc;margin-bottom:12px !important;margin-top:12px !important;padding:0px 0px 0px 0px;">
977	                    <li class="listItem ultext">
978	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
979	                      Tuesday
980	                     </p>
981	                    </li>
982	                    <li class="listItem ultext">
983	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
984	                      <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.JbzN4EeDIx5uxJlO_mtQ8IXDUdHMot_lw-QuQhiWtikXN7L8gD0HISyAgIcjpOXJHWXw5s67WxJAGg3_SvHMBWSCTgjaFl3e-2EYBd4XOazNyxFsJESYcgO0dNRveyB-Hmh7ZXDljfa_cC6PMifmeBgINOeEfQSO14fqxC6OPUu833CncJQCGnVo_1nvDuasL3olJvhy5raIj8PUhPKuUT8RklODA5thUDSgR014vPiuFo64VasENRmzQz1Xscd1/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h17/h001.-oXJqMMelZiQDVd_2wtHbUj33PLyNB-3nFLWv8_JNjQ" rel="noopener noreferrer nofollow" target="_blank">
985	                       <span>
986	                        RSVP
987	                       </span>
988	                      </a>
989	                     </p>
990	                    </li>
991	                   </ul>
992	                  </div>
993	                 </td>
994	                </tr>
995	                <tr>
996	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
997	                  <p style="mso-line-height-alt:150.0%;">
998	                   <b>
999	                    1 Million Cups Chicago
1000	                   </b>
1001	                  </p>
1002	                 </td>
1003	                </tr>
1004	                <tr>
1005	                 <td class="ee" style="padding-bottom:12px;padding-left:47px;padding-right:37px;padding-top:12px;">
1006	                  <div class="edm_outlooklist" style="margin-left:0px;">
1007	                   <ul style="font-weight:normal;list-style-type:disc;margin-bottom:12px !important;margin-top:12px !important;padding:0px 0px 0px 0px;">
1008	                    <li class="listItem ultext">
1009	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
1010	                      Wednesday
1011	                     </p>
1012	                    </li>
1013	                    <li class="listItem ultext">
1014	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
1015	                      <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.JbzN4EeDIx5uxJlO_mtQ8M-paNeOcv0vQuwWhWUY_oZXxjUz7FQeZV6JtbjopjtvHsWYWPWjXa7uZqCC62pfK8Z-MxCsP8vmrd7iVLiWakWA7YIDTFlxnJr8lVpoN4PMo39DDvizkzn557YRhhEgLePEA_g-3S3NIc8pyMSUFWizDMx5wsTPMXvoVHzTfm8W05O5eYQg-8_JiDnWvpdZavkG0MXWgaSGJtAl4soZ6-TVGyEbvYiRBeVZze-AUwUe/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h18/h001.sH0Af3BAqJMcqSFfQEqX4Km3IoHIkn0eJvAE_mfOu_o" rel="noopener noreferrer nofollow" target="_blank">
1016	                       <span>
1017	                        RSVP
1018	                       </span>
1019	                      </a>
1020	                     </p>
1021	                    </li>
1022	                   </ul>
1023	                  </div>
1024	                 </td>
1025	                </tr>
1026	                <tr>
1027	                 <td align="left" class="dd" style="padding:0px 25px;text-align:left;word-break:break-word;">
1028	                  <p style="mso-line-height-alt:150.0%;">
1029	                   <b>
1030	                    Techne Chicago
1031	                   </b>
1032	                  </p>
1033	                 </td>
1034	                </tr>
1035	                <tr>
1036	                 <td class="ee" style="padding-bottom:12px;padding-left:47px;padding-right:37px;padding-top:12px;">
1037	                  <div class="edm_outlooklist" style="margin-left:0px;">
1038	                   <ul style="font-weight:normal;list-style-type:disc;margin-bottom:12px !important;margin-top:12px !important;padding:0px 0px 0px 0px;">
1039	                    <li class="listItem ultext">
1040	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
1041	                      Wednesday
1042	                     </p>
1043	                    </li>
1044	                    <li class="listItem ultext">
1045	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
1046	                      The first 10 people who email
1047	                      <a class="link" clicktracking="off" href="mailto:hello@techechicago.com" rel="noopener noreferrer nofollow" target="_blank">
1048	                       <span>
1049	                        hello@techechicago.com
1050	                       </span>
1051	                      </a>
1052	                      and mention Drive Capital or Landon’s Loop will get a comp ticket
1053	                     </p>
1054	                    </li>
1055	                    <li class="listItem ultext">
1056	                     <p style="mso-line-height-alt:150.0%;padding:0px;text-align:left;word-break:break-word;">
1057	                      <a class="link" href="https://link.mail.beehiiv.com/ss/c/u001.E_InkxgnjWj38C_b_rz0Ey8QtNmm9xiwAHl4z9Bzr9aHZFnVBnF-lgAYHg4Az-V5vAgOyXrpR5jkzVj1bKYhsq4weelK6mAxJD1HGW2OR_lG1LTjz2YYnzy_TOmxx9Iz-f75AN1brrGDTdoAzC38ocx_IpHH6F1_GUdKZixBgUOEjvGerUsJMr0iLvm2E01fY1QflqAiOwfyaSQOTV7tlBAVVl1FxZhYH9SBzh_05ZY7f1mu_Tk7DkXM4UWIxKgC/4l3/ykpiFGyaRg-oEJ-o8ihb6Q/h19/h001.URswNlUoLfv19SYYRzfOTs974aZCtYS3lT2sxCOzkNg" rel="noopener noreferrer nofollow" target="_blank">
1058	                       <span>
1059	                        RSVP
1060	                       </span>
1061	                      </a>
1062	                     </p>
1063	                    </li>
1064	                   </ul>
1065	                  </div>
1066	                 </td>
1067	                </tr>
</Example2>

In this case, if the user asked for "Chicago tech events", you would make each one of the four news items into a separate item, unless specifically instructed otherwise.
Make sure to include all of them, for instance, in this case, you would return the following pairs of start and end numbers:
StartLine = 933, EndLine = 963
StartLine = 964, EndLine = 994
StartLine = 995, EndLine = 1025
StartLine = 1026, EndLine = 1067

Note that you would NOT include the title span ("This Week’s Chicago Tech Events").

Use your judgement and each section's description to determine whether to extract multiple items or a single item per section.
It is possible for a section to have zero items if no matching content is found.
"""

# Used in generate_content_insights() — user message template containing
# instructions and an example report for analyzing item CTR performance.
INSIGHTS_PROMPT = """
<instructions>
You are an expert newsletter analyst.

You have been given a list of items that appeared in a newsletter.
Each item has a name/description, CTR, and a percentile rank.

Write a concise performance report following the sample structure below.

Rules:
- Do not include item IDs.
- Use markdown tables for example items; single-sentence bullets for traits.
- If only one section is present, omit the Overall block and per-section headers — output just the archetype analysis directly.
- Show up to 5 examples per high/low block. Keep bullet lists to 2–3 points each.
   These do not necessarily need to be the absolute top or bottom performing items within the section.
   Rather, you should first decide what the characteristics of high- and low-performing items are within each section and THEN identify up to 5 examples that showcase these trends.
- Shorten long items to a headline label (≤10 words). Keep the key hook. For example: 
   Too long: "A framework for reliable browser-using agents Notte is a production‑oriented framework for building browser-using web automation agents, intended to be easier and cheaper to use at scale than alternatives like Browser Use and Convergence”
   Better: "Notte: a framework for browser-using agents"
</instructions>

<sample>
## Summary
Your audience is most interested in community events with a social or hands-on builder angle consistently drive the highest click-through rates, especially when tied to recognized brands or concrete outcomes. Finance, crypto, and policy-focused events with abstract titles tend to underperform significantly.

## 📈 High performers
|| CTR | Percentile |
|------|-----|:---:|
| Chicago Tech Mixer | 9.6% | 100% |
| From Idea to MVP | 9.3% | 97% |
| Chicago Coffee Club: Vertical AI Founders | 9.3% | 97% |
| ML Reading Group Social Hour | 9.2% | 94% |
| Context Engineering w/ Pinecone | 9.0% | 92% |

**✅ What works:**
- Social/community framing with a clear AI/tech audience ("mixer," "happy hour," "collective").
- Concrete outcome tied to goals tech founders might want to achieve: "Idea to MVP," "Building an MCP."
- Attached to a prestigious brand or known community (Drive Capital, Pinecone, AI Tinkerers).

## 📉 Low performers
|| CTR | Percentile |
|------|-----|:---:|
| Chicago Stablecoin Social | 2.0% | 2% |
| Blockchain & Digital Assets: Policy Trends | 2.6% | 13% |
| Money Moves: Future of Investment Mgmt | 2.5% | 10% |
| 1 Million Cups Chicago | 1.8% | 1% |
| Java Global Insights: Innovation | 2.1% | 4% |

**❌ What doesn't work:**
- Finance/crypto/policy framing with no builder or practitioner angle.
- Abstract titles with no specific benefit ("Outlook," "Innovation," "Insights").
- Non-Python languages like Java or Haskell
</sample>
"""

# Used in annotate_post_html() — system message instructing the LLM to
# identify underperforming content and suggest improvement tips.
TIP_PROMPT = """
You have been given an HTML document with line numbers and performance evaluation(s) of similar content.
Your task is to identify pieces of the content which are most likely to have the LOWEST (worst) click rates based on the evaluations, 
and suggest tips that could be inserted into the HTML to help the writer improve engagement based on the performance insights.

First, identify up to 6 places in the HTML where the content most closely follows the negative patterns described in the performance evaluations or deviates furthest from high-performing patterns.
Ignore content that is obviously an ad; evaluate only the main article content.
Second, for each identified place, determine whether the content can be re-worded for clarity/engagement (Wording Tip) or if the content itself is likely to draw poor engagement (Content Tip).
Finally, for each identified place, suggest a tip to improve it and why the tip is relevant based on the performance evaluations.

There are 2 types of tips you can provide:
1. Wording Tip: Suggested changes to the choice of words or phrasing.
    Wording tip example:
    tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'
    why: "Advice that takes a clear stance on when and how to use biological controls almost always does better with your audience than neutral articles."

2. Content Tip: Suggested changes to the information presented.
    Content tip example:
    tip_text: "Consider instead featuring an article that focuses on practical advice for gardeners considering pesticide use."
    why: "Your readers usually prefer content about the specific risks of using pesticides on your own garden over content about broad environmental impacts of pesticides."

tip_text should be a single brief sentence suggesting an actionable change.
why: should be a single brief sentence explaining the rationale based on performance insights.

Provide the tip type, the line number where each tip should be inserted, the tip text, and the why for each tip.
Don't cite item IDs from the report--the user won't have access to that information.
DO NOT suggest changes to the format of the newsletter, just the type of items written about and how they are worded.
You should NOT start the text of the tip itself with the tip type; this will be added later based on the tip type.
In the why, refer to "your audience", "your readers", etc. to ensure the writer understands this is personalized to their specific audience.

Use language suitable for content creators, avoiding technical jargon and esoteric wording.

Too advanced:
tip_text: "Strengthen this link by foregrounding a clear mental model or framework readers will get (e.g., "how to decide between biological controls and pesticides in real projects")."
why: "Opinionated guidance on when/how to use biological controls consistently outperforms neutral articles with your audience."

Good:
tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'"
why: "Advice that takes a clear stance on when and how to use biological controls almost always does better with your audience than neutral articles."

Place the tips DIRECTLY BELOW the specific content being referenced.
An arrow indicator will be added above the tip to indicate its placement--that arrow should not be included in your tip text.
Think carefully about what line number to assign to each tip so that it appears directly below the relevant content.
"""