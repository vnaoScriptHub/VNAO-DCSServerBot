import discord
import html2image
import jinja2
import os
import psycopg
import tempfile
import uuid

from contextlib import closing
from core import EmbedElement
from datetime import datetime
from io import BytesIO
from pathlib import Path
from psycopg.rows import dict_row

class Greenie(EmbedElement):
    async def render(self, server_name: str, config: dict, trap_data: dict, squadron_data: dict):
        self.log.debug("Rendering Greenieboard report.")

        # These are used for setting the size of the final image file so that the final image
        # is only as big as it needs to be depending on number of rows in the board and how
        # wide the longest pilot name is.
        board_width_min = 745 # Empty board is 745px in width
        board_height_min = 80 # Empty board is 80px in height
        board_pilot_width_empty = 123 # Empty pilot column is 123px wide
        board_char_width = 10 # pixel width of 1 char
        board_row_height = 25 # Each board row is 25px tall
        row_cnt = 0 # How many rows on the board
        longest_name_len = 0 # How long the longest name is
        footer_addition = ""

        board_data: list = []
        board_legend = f"{config['greenie_boards']['legend_glyphs']['green']}OK \t{config['greenie_boards']['legend_glyphs']['yellow']}Fair \t{config['greenie_boards']['legend_glyphs']['blue']}Bolter \t{config['greenie_boards']['legend_glyphs']['brown']}NoGrade \t{config['greenie_boards']['legend_glyphs']['black']}WaveOff \t{config['greenie_boards']['legend_glyphs']['red']}Cut \t{config['greenie_boards']['icons']['unicorn']}-Unicorn \t{config['greenie_boards']['icons']['case2']}-Case2 \t{config['greenie_boards']['icons']['case3']}-Case3"

        # if squadron_flt == True:
        if squadron_data['is_squadron_flight'] == True:
            jinja2_board_template = config['greenie_boards']['jinja2_template_squadron']
            css_file = f"{config['greenie_boards']['jinja2_templates_folder']}/{config['greenie_boards']['jinja2_css_squadron']}"
            board_name = f"{squadron_data['squadron_name']} ({config['greenie_boards']['aircraft'][trap_data['airframe']].upper()})"
            footer_addition = "\nSquadron boards contain traps of flights longer than 20mins."
            self.embed.title = f"{squadron_data['squadron_name']}"
            self.embed.set_thumbnail(url=config['greenie_boards']['squadron_tags'][squadron_data['squadron_tag']]['emblem'])
            self.log.debug("Greenieboard type: Squadron")
        else:
            jinja2_board_template = config['greenie_boards']['jinja2_template_practice']
            css_file = f"{config['greenie_boards']['jinja2_templates_folder']}/{config['greenie_boards']['jinja2_css_practice']}"
            board_name = f"{trap_data['airframe'].upper()} (Practice)"
            self.embed.title = f"{trap_data['airframe'].upper()} (Practice)"
            self.log.debug("Greenieboard type: practice")
        
        board_title = {"name": board_name, "img": ""}
        self.log.debug(board_title)
        
        sql_traps_average = f"""
                    SELECT
                        player_name,
                        round(avg(points), 1) AS avg,
                        Count(*)
                    FROM
                    """
        if squadron_data['is_squadron_flight'] == True:
            sql_traps_average += f"""
                    greenie_board_data_squadron('{server_name}', '{trap_data['airframe']}','%{squadron_data['squadron_tag']}%',{config['greenie_boards']['squadron_time_min']})
                """
            # self.log.debug(sql_traps_average)
        else:
            sql_traps_average += f"""
                    greenie_board_data_practice('{server_name}', '{trap_data['airframe']}','%',0)
                """
        sql_traps_average += f"""
                GROUP BY
                    player_name
                ORDER BY
                    avg DESC,
                    count DESC
                """

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(sql_traps_average)
                
                if cursor.rowcount > 0:
                    # Total number of rows for the board
                    row_cnt = cursor.rowcount

                    # Temp counter for sql filename, debugging
                    # cnt = 0

                    for row in cursor.fetchall():

                        # Keeping track of the longest name to be displayed on board
                        current_name_len = len(row['player_name'])
                        if current_name_len > longest_name_len:
                            longest_name_len = current_name_len

                        sql_traps = f"""
                            SELECT
                                server_name,
                                player_name,
                                flightlog_id,
                                aircraft_type,
                                case_num,
                                round(points, 1) AS points,
                                server_timestamp
                            FROM
                            """
                        if squadron_data['is_squadron_flight'] == True:
                            sql_traps += f"""
                                    greenie_board_data_squadron('{server_name}', '{trap_data['airframe']}','%{row['player_name']}%',{config['greenie_boards']['squadron_time_min']},{config['greenie_boards']['trap_column_count']})
                             """
                        else:
                            sql_traps += f"""
                                    greenie_board_data_practice('{server_name}', '{trap_data['airframe']}','{row['player_name']}',0)
                                """
                        sql_traps += f"""
                            GROUP BY
                                flightlog_id,
                                server_name,
                                player_name,
                                aircraft_type,
                                case_num,
                                points,
                                server_timestamp
                            ORDER BY
	                            server_timestamp DESC
                            LIMIT
                                30
                            """

                        # Debugging
                        # cnt += 1
                        # with open(f"d:/sql_{cnt}.sql", "w") as fh:
                        #     fh.write(sql_traps)

                        cursor.execute(sql_traps)

                        # add the traps to the pilots row
                        row['traps'] = cursor.fetchall()

                        # determine color for grade and icon for unicorn, case 1 and case 2
                        for trap in row['traps']:
                            background_color: str = ""
                            icon_text: str = ""

                            # self.log.debug(f"trap data: {trap}")

                            # Determine text icon
                            # self.log.debug(f"trap case: {trap['case_num']}")
                            if trap['points'] == 5:
                                # unicorn
                                icon_text = config['greenie_boards']['icons']['unicorn']
                            elif trap['case_num'] == 2:
                                # case 2 unicode
                                icon_text = config['greenie_boards']['icons']['case2']
                            elif trap['case_num'] == 3:
                                # case 3 unicode
                                icon_text = config['greenie_boards']['icons']['case3']

                            # Determine column background color
                            # self.log.debug(f"trap point: {trap['points']}")
                            match trap['points']:
                                case 5:
                                    background_color = config['greenie_boards']['colors']['green']
                                case 4:
                                    background_color = config['greenie_boards']['colors']['green']
                                case 3:
                                    background_color = config['greenie_boards']['colors']['yellow']
                                case 2.5:
                                    background_color = config['greenie_boards']['colors']['blue']
                                case 2:
                                    background_color = config['greenie_boards']['colors']['brown']
                                case 1:
                                    background_color = config['greenie_boards']['colors']['black']
                                case 0:
                                    background_color = config['greenie_boards']['colors']['red']

                            trap['icon'] = icon_text
                            trap['color_background'] = background_color

                        # add the pilot row to the main list
                        board_data.append(row)
                else:
                    row_cnt = 0
                    self.log.debug('No pilots found when creating greeniboard!')


            # Create a temporary Windows folder and files for jinja2 and html2image output.
            # Must be manually cleanup the temporary folder before returning from this method.
            temp_folder = tempfile.TemporaryDirectory(prefix="DCSServerBot.vnao.")
            temp_png = Path(tempfile.NamedTemporaryFile(delete=False, dir=temp_folder.name, suffix='.png').name)
            temp_html = Path(tempfile.NamedTemporaryFile(delete=False, dir=temp_folder.name, suffix='.html').name)
            
            # self.log.debug(f"Exists - temp_png:{Path.exists(temp_png)}  temp_html:{Path.exists(temp_png)}")

            # temp_board_image.close()
            # temp_path = Path(temp_board_image.name)
            # self.log.debug(f"Temp png file: {temp_png}")
            # self.log.debug(f"Temp html file: {temp_html}")
                
            # Generate a temp html file.
            # jinja2_board_template = config['greenie_boards']['jinja2_template_strafe']
            jinja2_file_loader = jinja2.FileSystemLoader(config['greenie_boards']['jinja2_templates_folder'])
            jinja2_env = jinja2.Environment(loader=jinja2_file_loader)
            jinja2_template = jinja2_env.get_template(jinja2_board_template)
            temp_html.write_text(jinja2_template.render(pilots=board_data, legend=board_legend, board_meta=board_title))

            # Determine what the image dimensions need to be based on number of rows
            # and the longest pilot name.
            board_height = board_height_min + (row_cnt * board_row_height) # in pixels

            name_width = longest_name_len * board_char_width # in pixels
            if name_width > board_pilot_width_empty:
                # name is longer than 123px min so we need to add the differene to the board width
                board_width = board_width_min + (name_width - board_pilot_width_empty)
            else:
                board_width = board_width_min

            self.log.debug(f"Creating html2image files.")

            # Converts the html file into a png
            # html2image can have issues with certain versions of Chrome.  To fix this issue an older version
            # of chrome is used as it is known to work correctly.
            # Download this version and install:
            #  - https://sourceforge.net/projects/portableapps/files/Google%20Chrome%20Portable/GoogleChromePortable64_109.0.5414.120_online.paf.exe/download
            # Update the browser_exectuable path parameter to it's location and it should work.
            if css_file:
                hti = html2image.Html2Image(browser_executable="C:\\Users\\vnaon\\GoogleChromePortable64\\GoogleChromePortable.exe",
                                            custom_flags=['--default-background-color=00000000', '--hide-scrollbars'])
                hti.output_path = temp_png.parent
                # hti.temp_path = temp_folder.name
                self.log.debug(f"html2image output_path: {hti.output_path}")
                hti.screenshot(
                    html_file=str(temp_html),
                    css_file=css_file,
                    size=(board_width, board_height),
                    save_as=temp_png.name,
                )
            else:
                self.log.debug(f"CSS file could not be determined based on board of type")

            # Build the embed footer and timestamp
            footer = ("\nNew traps are placed at the front, meaning first is your latest trap.")
            footer += footer_addition or ""
            footer += f"\n\nLast recorded trap"
            self.embed.set_footer(text=footer)
            self.embed.timestamp = datetime.now()

            # Create a bytearray of the image and add the bombboard image to the embed
            with open(temp_png, 'rb') as fh:
                buf: BytesIO = BytesIO(fh.read())
            self.env.buffer = buf
            self.env.filename = temp_png.name
            # self.log.debug(f"attachment://{temp_png.name}")
            self.embed.set_image(url=f"attachment://{temp_png.name}")

            # This will remove the temp files that were created
            temp_folder.cleanup()
                
class Range(EmbedElement):
    async def render(self, server_name: str, config: dict, board_type: str):
        self.log.debug("Rendering range report.")

        # These are used for setting the size of the final image file so that the final image
        # is only as big as it needs to be depending on number of rows in the board and how
        # wide the longest pilot name is.
        board_width_min = 745 # Empty board is 745px in width
        board_height_min = 80 # Empty board is 80px in height
        board_pilot_width_empty = 123 # Empty pilot column is 123px wide
        board_char_width = 10 # pixel width of 1 char
        board_row_height = 25 # Each board row is 25px tall
        row_cnt = 1 # How many rows on the board
        longest_name_len = 0 # How long the longest name is

        if board_type == "bomb":
            db_table = "vnao_bombboard"
            board_title = "Bomb Board"
            board_legend = f"{config['range_boards']['legend_glyphs']['blue']}Shack \t{config['range_boards']['legend_glyphs']['green']}Excellent \t{config['range_boards']['legend_glyphs']['yellow']}Good \t{config['range_boards']['legend_glyphs']['brown']}Ineffective \t{config['range_boards']['legend_glyphs']['red']}Poor"
            jinja2_type_template = config['range_boards']['jinja2_template_bomb']
            css_file = f"{config['range_boards']['jinja2_templates_folder']}/{config['range_boards']['jinja2_css_bomb']}"
            self.embed.title = "Bomb Board"
            # self.embed.set_thumbnail(url=)
            self.log.debug("Range board type: Bomb")
        else:
            db_table = "vnao_strafeboard"
            board_title = "Strafe Board"
            board_legend = f"{config['range_boards']['legend_glyphs']['blue']}Deadeye \t{config['range_boards']['legend_glyphs']['green']}Excellent \t{config['range_boards']['legend_glyphs']['yellow']}Good \t{config['range_boards']['legend_glyphs']['brown']}Ineffective \t{config['range_boards']['legend_glyphs']['red']}Poor \t{config['range_boards']['legend_glyphs']['red_x']}Invalid - Passed Foul Line"
            jinja2_type_template = config['range_boards']['jinja2_template_strafe']
            css_file = f"{config['range_boards']['jinja2_templates_folder']}/{config['range_boards']['jinja2_css_strafe']}"
            self.embed.title = "Strafe Board"
            # self.embed.set_thumbnail(url=)
            self.log.debug("Range board type: Strafe")
        
        board_data: list = []
        board_title = {"name": board_title, "img": ""}

        sql1 = 'SELECT g.player_ucid, p.name, g.points, MAX(g.time) AS time FROM (' \
                'SELECT player_ucid, ROW_NUMBER() OVER w AS rn, AVG(points) OVER w AS points, MAX(time) ' \
               f'OVER w AS time FROM {db_table}'
        sql2 = f'SELECT TRIM(quality) as "quality" FROM {db_table} WHERE player_ucid = %s AND EXTRACT(YEAR FROM {db_table}.TIME) = EXTRACT(YEAR FROM NOW()) '\
                f'AND EXTRACT(MONTH FROM {db_table}.TIME) = EXTRACT(MONTH FROM NOW())'
        
        if server_name:
            sql1 += f" WHERE mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}') AND EXTRACT(YEAR FROM {db_table}.TIME) = EXTRACT(YEAR FROM NOW()) AND EXTRACT(MONTH FROM {db_table}.TIME) = EXTRACT(MONTH FROM NOW())"
            sql2 += f" AND mission_id in (SELECT id FROM missions WHERE server_name = '{server_name}')"
        sql1 += ' WINDOW w AS (PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING)) ' \
                'g, players p WHERE g.player_ucid = p.ucid AND g.rn = 1 GROUP BY 1, 2, 3 ORDER BY 3 DESC'
        sql2 += ' ORDER BY ID DESC LIMIT 30'

        self.log.debug(f'SQL 1:/n{sql1}')
        self.log.debug(f'SQL 2:/n{sql2}')

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(sql1)
                if cursor.rowcount > 0:
                    row_cnt = cursor.rowcount
                    
                    pilots = points = count = ''
                    max_time = datetime.fromisocalendar(1970, 1, 1)
                    for row in cursor.fetchall():
                        # Keeping track of the longest name to be displayed on board
                        current_name_len = len(row['name'])
                        if current_name_len > longest_name_len:
                            longest_name_len = current_name_len


                        cursor.execute(sql2, (row['player_ucid'], ))
                        row["points"] = round(row["points"],1)
                        row["passes"] = cursor.fetchall()
                        for current_pass in row["passes"]:
                            background_color: str = ""
                            icon_text: str = ""

                            # Determine column background color
                            # self.log.debug(f'trap point: {trap["points"]}')
                            match current_pass['quality']:
                                # bomb quality
                                case 'SHACK':
                                    background_color = config['range_boards']['colors']['blue']
                                case 'EXCELLENT':
                                    background_color = config['range_boards']['colors']['green']
                                case 'GOOD':
                                    background_color = config['range_boards']['colors']['yellow']
                                case 'INEFFECTIVE':
                                    background_color = config['range_boards']['colors']['brown']
                                case 'POOR':
                                # strafe quality
                                    background_color = config['range_boards']['colors']['red']
                                case 'DEADEYE PASS':
                                    background_color = config['range_boards']['colors']['blue']
                                case 'EXCELLENT PASS':
                                    background_color = config['range_boards']['colors']['green']
                                case 'GOOD PASS':
                                    background_color = config['range_boards']['colors']['yellow']
                                case 'INEFFECTIVE PASS':
                                    background_color = config['range_boards']['colors']['brown']
                                case 'POOR PASS':
                                    background_color = config['range_boards']['colors']['red']

                            current_pass["icon"] = icon_text
                            current_pass["color_background"] = background_color  

                        board_data.append(row)
                        # self.log.debug(board_data)
                else:
                    match board_type:
                        case "bomb":
                            self.log.debug('No bomb grades found!')
                        case "strafe":
                            self.log.debug('No strafe grades found!')                    
                    
            # Create a temporary Windows folder and files for jinja2 and html2image output.
            # Must be manually cleanup the temporary folder before returning from this method.
            temp_folder = tempfile.TemporaryDirectory(prefix="DCSServerBot.vnao.")
            temp_png = Path(tempfile.NamedTemporaryFile(delete=False, dir=temp_folder.name, suffix='.png').name)
            temp_html = Path(tempfile.NamedTemporaryFile(delete=False, dir=temp_folder.name, suffix='.html').name)
            # temp_board_image.close()
            # temp_path = Path(temp_board_image.name)
            self.log.debug(f"Temp png file: {temp_png}")
            self.log.debug(f"Temp html file: {temp_html}")

            self.log.debug(f'{board_data}')
            # Generate a temp html file.
            jinja2_file_loader = jinja2.FileSystemLoader(config['range_boards']['jinja2_templates_folder'])
            jinja2_env = jinja2.Environment(loader=jinja2_file_loader)
            jinja2_template = jinja2_env.get_template(jinja2_type_template)
            temp_html.write_text(jinja2_template.render(pilots=board_data, legend=board_legend, board_meta=board_title))

            # Determine what the image dimensions need to be based on number of rows
            # and the longest pilot name.
            board_height = board_height_min + (row_cnt * board_row_height) # in pixels

            name_width = longest_name_len * board_char_width # in pixels
            if name_width > board_pilot_width_empty:
                # name is longer than 123px min so we need to add the differene to the board width
                board_width = board_width_min + (name_width - board_pilot_width_empty)
            else:
                board_width = board_width_min

            self.log.debug(f"Creating html2image files.")

            # Convert the html file into a png
            # html2image can have issues with certain versions of Chrome.  To fix this issue an older version
            # of chrome is used as it is known to work correctly.
            # Download this version and install:
            #  - https://sourceforge.net/projects/portableapps/files/Google%20Chrome%20Portable/GoogleChromePortable64_109.0.5414.120_online.paf.exe/download
            # Update the browser_exectuable path parameter to it's location and it should work.
            if css_file:
                hti = html2image.Html2Image(browser_executable="C:\\Users\\vnaon\\GoogleChromePortable64\\GoogleChromePortable.exe",
                                            custom_flags=['--default-background-color=00000000', '--hide-scrollbars'])
                hti.output_path = temp_png.parent
                hti.screenshot(
                    html_file=str(temp_html),
                    css_file=css_file,
                    size=(board_width, board_height),
                    save_as=temp_png.name
                )
            else:
                self.log.debug(f'CSS file could not be determined based on board of type')

            # Build the footer and add a timestamp
            footer = '\nLatest passes are added at the front, meaning first column is your last run.'
            footer += f"\n\nLAST RECORDED PASS"
            self.embed.set_footer(text=footer)
            self.embed.timestamp = datetime.now()

            # Create a bytearray of the image and add the bombboard image to the embed
            with open(temp_png, 'rb') as fh:
                buf: BytesIO = BytesIO(fh.read())
            self.env.buffer = buf
            self.env.filename = temp_png.name
            self.embed.set_image(url=f"attachment://{temp_png.name}")

            # This will remove the temp files that were created
            temp_folder.cleanup()
