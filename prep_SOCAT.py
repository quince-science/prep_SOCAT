#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 18 13:54:03 2025

@author: Jean Negrel (jean.negrel@norceresearch.no), NORCE Research AS, Bergen
"""

# Major version number.
__major__ = '1'
# Minor version number.
__minor__ = '1'
# Script version.
__version__ = __major__ + '.' + __minor__

# TODO: Do some cleaning of the script and add error management

import os
import sys
import xml.etree.ElementTree as ET
import json
import zipfile
import tempfile
from datetime import datetime as dt
from icoscp_core.icos import meta
import shutil

# Run a QuinCE API call to download the dataset corresponding to the given filename
#
def QuinCe_API(dataset_name, data_file):
    import requests
    from requests.auth import HTTPBasicAuth

    # Load configuration for the QuinCe instance
    try:
        with open(os.path.join(base_path, 'credentials.json')) as f:
            cred = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("Error loading credentials file:", e)
        sys.exit(3)
    url = cred['url']
    username = cred['username']
    password = cred['password']
    
    # Connect to the QuinCe API
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    response = session.post(url + 'export/downloadDataset', data={'datasetName': dataset_name}, stream=True)
    # if the connection has been successful, retrieve the dataset
    if response.status_code == 200:
        print('Connection successful!')
        if len(response.text) > 22: # Check the file is not "empty", limit set arbitrarily to minimum zip file size
            try:
                with open(data_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                fsize = len(response.content)
                if fsize >= 1024:
                    if fsize >= 1024*1024:
                        size_str = 'MB'
                        fsize = fsize/(1024*1024)
                    else:
                        size_str = 'kB'
                        fsize = fsize/1024
                else:
                    size_str = 'bytes'
                print("File downloaded successfully! (%5.2f %s written)" % (fsize, size_str))
            except requests.exceptions.RequestException as e:
                print("Error downloading the dataset file:", e)
                sys.exit(4)
        else:
            print('Looks like the zip file is empty...')
            sys.exit(4)
    else:
        print(f"Error: connection failed! Status code: {response.status_code}")
        print("Response content:\n", response.content)
        sys.exit(4)
    
# Read template xml file and return it
#
def import_xml(xml_file):
    try:
        tree = ET.parse(xml_file)
    except (FileNotFoundError, ET.ParseError) as e:
        print('Error loading the template file: ', xml_file, e)
        sys.exit(5)
    root = tree.getroot()
    return root

# Unzip the archive exported from QuinCe into the temporary folder
#
def unzip_data(data_file):
    try:
        with zipfile.ZipFile(data_file, 'r') as zipf:
            zipf.extractall(tmp_path)
    except zipfile.Error as e:
        print('Error while unzipping the dataset file: ', e)
        sys.exit(5)
    try:
        os.remove(data_file)
    except OSError as e:
        print('Warning: the zip file could not be removed: ', e)

# Unpack the archive exported from QuinCe into the temp folder (which also makes
# the .tsv data file available) and load the metadata from the manifest.json file.
#
def import_metadata(tmp_folder, data_file):
    unzip_data(data_file)
    manifest_file = os.path.join(tmp_folder, 'manifest.json')
    try:
        with open(manifest_file, 'r') as file:
            metadata = json.load(file)
    except (OSError, json.JSONDecodeError) as e:
        print('Error: could not load "manifest.json" metadata: ', e)
        sys.exit(5)
    return metadata

def get_CP_metadata(filename, start_date, end_date):
    _DATA_TYPES = """
        <http://meta.icos-cp.eu/resources/cpmeta/icosOtcL2Product>
        <http://meta.icos-cp.eu/resources/cpmeta/icosOtcFosL2Product>
    """
    _QUERY_PREFIX = """prefix cpmeta: <http://meta.icos-cp.eu/ontologies/cpmeta/>
                     prefix otcmeta: <http://meta.icos-cp.eu/ontologies/otcmeta/>
                     prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
                     prefix prov: <http://www.w3.org/ns/prov#>
                     prefix xsd: <http://www.w3.org/2001/XMLSchema#>"""
    query = f"""{_QUERY_PREFIX}
    SELECT ?dobj ?timeStart ?timeEnd WHERE {{
    VALUES ?spec {{ {_DATA_TYPES} }}
    ?dobj cpmeta:hasObjectSpec ?spec .
    ?dobj cpmeta:hasName ?fileName .
    ?dobj cpmeta:hasStartTime | (cpmeta:wasAcquiredBy / prov:startedAtTime) ?timeStart .
    ?dobj cpmeta:hasEndTime | (cpmeta:wasAcquiredBy / prov:endedAtTime) ?timeEnd .
    FILTER(
        ?timeStart >= "{start_date}"^^xsd:dateTime &&
        ?timeEnd <= "{end_date}"^^xsd:dateTime
    )
    FILTER (CONTAINS(str(?fileName), "{filename}"))
    }}
    """
    try:
        query_result = meta.sparql_select(query)
    except Exception as e:
        print('Error while running SPARQL query: ', e)
        sys.exit(6)
    if len(query_result.bindings) == 0:
        print('Error: SPARQL query returned no data')
        sys.exit(6)
    metadata['manifest']['CP'] = {'URI': query_result.bindings[0]["dobj"].uri,
                                  'PID': query_result.bindings[0]["dobj"].uri.split("/")[-1]
                                  }
    return metadata
    
# Fill the missing data in the template xml file with data found in the metadata
# and/or datafile
#
# TODO: concentration gas
#
def get_value(tag, metadata):
    if 'metadataRecordCreationDate' in tag:
        value = dt.today().date().isoformat()
    elif 'submissionDate' in tag:
        value = metadata['manifest']['metadata']['last_touched'][:10]
    elif 'metadataURL' in tag:
        value = metadata['manifest']['CP']['URI']
    elif 'datasetURL' in tag:
        value = metadata['manifest']['CP']['URI']
    elif 'datasetDOI' in tag:
        value = metadata['manifest']['CP']['PID']
    elif 'startDate' in tag:
        value = metadata['manifest']['exportFiles']['SOCAT']['validStartDate'][:10] # Should the date be "rounded"?
    elif 'endDate' in tag:
        value = metadata['manifest']['exportFiles']['SOCAT']['validEndDate'][:10] # Same question?
    elif 'westernBounds' in tag:
        value = str(metadata['manifest']['exportFiles']['SOCAT']['validBounds']['west'])
    elif 'easternBounds' in tag:
        value = str(metadata['manifest']['exportFiles']['SOCAT']['validBounds']['east'])
    elif 'northernBounds' in tag:
        value = str(metadata['manifest']['exportFiles']['SOCAT']['validBounds']['north'])
    elif 'southernBounds' in tag:
        value = str(metadata['manifest']['exportFiles']['SOCAT']['validBounds']['south'])
    elif 'expocode' in tag:
        value = metadata['manifest']['metadata']['name']
    else:
        value = 'TK'
        print('Warning: unknown tag: ', tag, '... filled with default value')
    return value

# Recursive function that scans all the xml structure, looking for missing data
#
# NB: for some reason the test for leaf value within the loop causes the recursive
# call to crash (something to do with local variables). To be investigated further
# later on for optimisation.
#
def populate_xml(xml_data, metadata):
    if len(list(xml_data))==0:
            if xml_data.text == 'TK':
                xml_data.text = get_value(xml_data.tag, metadata)
    for child in xml_data:
        child = populate_xml(child, metadata)
    return xml_data

# Get the default namespace from the imported XML
def get_namespace_uri(el):
    # ChatGPT gave me this. I hope it works - Steve
    if el.tag.startswith("{"):
        return el.tag.split("}", 1)[0][1:]
    return None

# Save the completed xml file into the temporary folder
#
def save_xml(xml_data, tmp_folder):
    fname = os.path.join(tmp_folder, os.path.basename(tmp_folder) + '.xml')
    
    # We want to use the default (empty) namespace on export
    ET.register_namespace("", get_namespace_uri(xml_data))
    
    xml_str = ET.tostring(xml_data).decode()
    try:
        with open(fname, 'w') as xml_file:
            xml_file.write(xml_str)
    except OSError as e:
        print('Error: could not save the metadata to the xml file: ', e)
        sys.exit(5)
    return fname
        
# Find the value corresponding to a keys tree in the given xml etree.
# If several values are found, a semicolon separated string is returned
#
def find_leaf(xml_data, keys, multiple_entries=False):
    xml_prefix = xml_data.tag.replace('oads_metadata', '')
    s = xml_prefix + keys.replace('/', '/' + xml_prefix)
    leaf = []
    if multiple_entries:
        first = xml_data.findall(s + '/' + xml_prefix + 'first')
        last = xml_data.findall(s + '/' + xml_prefix + 'last')
        for f, l in zip(first, last):
            leaf.append(', '.join([l.text, f.text[0]]))
    else:
        for txt in xml_data.findall(s):
            leaf.append(txt.text)
    return '; '.join(leaf)

# Add the "SOCAT" header to the datafile
#
def write_header(tsv_file, xml_data):
    # Prepare the header from the xml metadata
    expocode = find_leaf(xml_data, 'expocodes/expocode')
    vessel = find_leaf(xml_data, 'platforms/platform/name')
    pi = find_leaf(xml_data, 'investigators/investigator/name', multiple_entries=True)
    vtype = find_leaf(xml_data, 'platforms/platform/type')
    h = (f"Expocode: {expocode}\nVessel name: {vessel}\nPIs: {pi}\n"
         f"Vessel type: {vtype} \n")
    try:
        # Read the content of the existing file
        with open(tsv_file, "r") as f:
            content = f.read()
        # Write header + existing content
        with open(tsv_file, "w") as f:
            f.write(h + content)
    except OSError as e:
        print('Error: could not save the data file header: ', e)
        sys.exit(5)

# Check if the folder exists and create it if not
#
def make_folder(folder):
    if (folder != '') & (folder != '.'):
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError as e:
                print("Error: could not create the folder:", e)
                sys.exit(3)
                
# Move the SOCAT tsv and xml files to the output folder.
def repack_preclean(tmp_folder):
    try:
        # Standardize paths to prevent slash mismatches
        tmp_folder = os.path.normpath(tmp_folder)
            
        # 1. Clean up top-level items
        flist = glob.glob(os.path.join(tmp_folder, '*'))
        for f in flist:
            # Match safely regardless of folder slash directions
            if ('.zip' in f) | ('/raw' in f):
                if os.path.isdir(f):
                    shutil.rmtree(f) 
                elif os.path.isfile(f):
                     os.remove(f)     

        # 2. Clean up dataset subfolder items
        dataset_folder = os.path.join(tmp_folder, 'dataset')
        flist = glob.glob(os.path.join(dataset_folder, '*'))
        for f in flist:
            if 'SOCAT' not in os.path.basename(f):
                if os.path.isdir(f):
                    shutil.rmtree(f)
                elif os.path.isfile(f):
                    os.remove(f)

    except OSError as e:
        print('Error: could not prepare the dataset for repacking: ', e)
        sys.exit(5)

# Recompress the data into a zip ready for upload.
#
def move_to_output(tsv_file, xml_file, out_folder):
    # Ensure the output folder exists before moving files into it
    make_folder(out_folder)
    print('Copying SOCAT files to output folder', end='...')
    try:
        shutil.move(tsv_file, out_folder)
        shutil.move(xml_file, out_folder)
    except OSError as e:
        print('Error: could not copy files to the output folder: ', e)
        sys.exit(5)
    print('Done')

# Clean up the temporary files to avoid unnecessary fill up of hard-drive
#
def clean_tmp(tmp_folder):
    try:
        shutil.rmtree(tmp_folder)
    except OSError as e:
        print('Warning: could not clean up the temporary files: ', e)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description = 'Script to prepare dataset for SOCAT upload.')
    parser.add_argument('-v', '--version', action = 'version',
                           version = '%(prog)s ' + __version__,
                           help = 'Print out script version and quit.')
    parser.add_argument('-n', '--name', type = str,
                        help = 'Filename associated to the dataset.')
    parser.add_argument('-S', '--SOCAT', '--template', type = str,
                        help = "SOCAT xml template file.\n"
                               "Defines the name of the xml template file containing all the metadata not retrieved from the Carbon Portal."
                               "The xml file should be available in the \"Data\" folder"
                               )
    parser.add_argument('-t', '--tmp', '--temp', type = str,
                        help = "(optional) Temp folder path.\n"
                               "Defines the folder used to temporarily store all downloaded data before processing."
                               "By default this value is set to the system temporary folder."
                               )
    parser.add_argument('-o', '--output', type = str,
                        help = "(optional) Output folder path.\n"
                               "Defines the folder where the final data and xml files containing the data ready for SOCAT import will be stored."
                               "By default this value is set to the dataset name"
                               )
    parser.add_argument('-d', '--data', type = str,
                        help = "(optional) Data folder path.\n"
                               "Defines the folder containing the base data (xml template and config file) for the script to work."
                               "By default this value is set to 'Data/'"
                               )
# The following will be used in future developments
    # parser.add_argument('-a', '--author', type = str,
    #                     help = '(optional) Name of the author of the dataset.')
    # parser.add_argument('-sd', '--startdate', type = str,
    #                     help = '(optional) Dataset starting date.')
    # parser.add_argument('-ed', '--enddate', type = str,
    #                     help = '(optional) Dataset ending date.')
    args = parser.parse_args()
    
    if args.name == None:
        print('Missing dataset name parameter! Unable to proceed...')
        parser.print_help()
        sys.exit(2)
    elif args.SOCAT== None:
        print('Missing template file parameter! Unable to proceed...')
        parser.print_help()
        sys.exit(2)
    else:
        dataset_name = args.name
        if '.zip' in dataset_name:
            zip_fname = dataset_name
            dataset_name = dataset_name[:-4]
        else:
            zip_fname = dataset_name + '.zip'
        xml_fname = args.SOCAT
        if '.xml' not in xml_fname:
            xml_fname = xml_fname + '.xml'

    # Retrieve arguments and define various paths and file names
    if args.tmp != None:
        tmp_path = args.tmp
    else:
        tmp_path = tempfile.gettempdir()
    if args.output != None:
        out_folder = args.output
    else:
        out_folder = dataset_name
    if args.data != None:
        base_path = args.data
    else:
        base_path = 'Data/'
    # Initialise file names with full path
    # Create potentially missing folders
    xml_file = os.path.join(base_path, xml_fname)
    tmp_folder = os.path.join(tmp_path, dataset_name)
    make_folder(tmp_folder)
    data_file = os.path.join(tmp_folder, zip_fname)
    tsv_fname = dataset_name + '.tsv'
    tsv_file = os.path.join(
        tmp_folder,
        'dataset',
        'SOCAT',
        tsv_fname
        )
    
    # Retrieve the dataset from QuinCe
    QuinCe_API(dataset_name, data_file)
    # Import the template xml file data
    xml_data = import_xml(xml_file)
    # Import the metadata from manifest.json and data from .tsv file
    metadata = import_metadata(tmp_folder, data_file)
    # Retrieve the missing metadata from CarbonPortal
    metadata = get_CP_metadata(
        metadata['manifest']['metadata']['name'],
        metadata['manifest']['metadata']['start'],
        metadata['manifest']['metadata']['end']
        )
    # Fill up the missing metadata in the xml file
    xml_data = populate_xml(xml_data, metadata)
    # write the xml file to disk
    out_xml_file = save_xml(xml_data, tmp_folder)
    # write the header to the tsv file
    write_header(tsv_file, xml_data)
    # Move the tsv and xml files to the output folder for import into SOCAT
    move_to_output(tsv_file, out_xml_file, out_folder)
    # Clean up after yourself :)
    clean_tmp(tmp_folder)
