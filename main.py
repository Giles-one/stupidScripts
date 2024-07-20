import os
import re
import json
import stat
import glob
import shutil
import zipfile

import tqdm
from tree_sitter import Language, Parser

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

trashPath     = 'AICyberGame/trash'
zipFilesPath  = 'AICyberGame/zipFiles'
jsonFilesPath = 'AICyberGame/jsonFiles'
saveTo        = 'AICyberGame/savedFile.json'
errorTo       = 'AICyberGame/errorFile.json'


languageMapping = {
    'c/c++'  : Language('tree-sitter/tree-sitter-cpp/libtree-sitter-cpp.so', 'cpp'),
    'python' : Language('tree-sitter/tree-sitter-python/libtree-sitter-python.so', 'python'),
    'java'   : Language('tree-sitter/tree-sitter-java/libtree-sitter-java.so', 'java'),
}

assert os.path.exists(jsonFilesPath)
assert os.path.exists(zipFilesPath)

def makeEmpthDir(dirPath):
    if os.path.exists(dirPath):
        shutil.rmtree(dirPath)
    os.makedirs(dirPath)
makeEmpthDir(trashPath)

def setReadableWritableDir(dirPath):
    for root, dirs, files in os.walk(dirPath):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            os.chmod(dir_path, stat.S_IRWXU)  # 用户可读、写、执行
        for file_name in files:
            file_path = os.path.join(root, file_name)
            os.chmod(file_path, stat.S_IRWXU)  # 用户可读、写、执行

def findJsonFileByDir(dirPath):
    jsonFiles = []
    for root, dirs, files in os.walk(dirPath):
        for file in files:
            if file.endswith(".json"):
                jsonFiles.append(os.path.join(root, file))
    jsonFiles.sort()
    return jsonFiles

def unzipProject(projectZipFilePath, unzippedProjectPath):
    with zipfile.ZipFile(projectZipFilePath, 'r') as zip_ref:
        if not any('\\' in fileName for fileName in zip_ref.namelist()):
            zip_ref.extractall(unzippedProjectPath)
            return
    cmd = 'unzip %s -d %s' % (projectZipFilePath, unzippedProjectPath)
    os.popen(cmd).read()

def getIdentifierInCAndCpp(node):
    identifier = None
    for child in node.children:
        if not child.type == 'function_declarator':
            continue
        for grandchild in child.children:
            if not grandchild.type == 'identifier':
                continue
            identifier = grandchild.text
            break
        break
    return identifier
def getIdentifierInJava(node):
    identifier = None
    for child in node.children:
        if not child.type == 'identifier':
            continue
        identifier = child.text
        break
    return identifier

def getIdentifierInPython(node):
    identifier = None
    for child in node.children:
        if not child.type == 'identifier':
            continue
        identifier = child.text
        break
    return identifier

def getIdentifierByRe(node):
    pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    nodeList = node.text.decode().split('\n')
    for lineOfCode in nodeList:
        if lineOfCode.strip().startswith('@'):
            continue
        if lineOfCode.strip().startswith('/*') \
                or lineOfCode.strip().startswith('*') \
                or lineOfCode.strip().startswith('*/'):
            continue
        match = re.search(pattern, lineOfCode)
        if not match:
            continue
        matchedText = match.group(1)
        return matchedText.encode()
    return None

def traverseTree(node, functionName, functionSourceCode, language):
    if functionSourceCode:
        return functionSourceCode

    if not functionName.encode() in node.text:
        return functionSourceCode

    # When we go here, that means functionName in node.text.
    # It indicates that it is either **function_definition** or **function_call**
    # C/C++ Handler
    if language == 'c/c++':
        if node.type == "function_definition":
            # watch point b'static void compile_xclass_matchingpath' in node.text
            identifier = getIdentifierInCAndCpp(node)
            if not identifier:
                identifier = getIdentifierByRe(node)
            if identifier == functionName.encode():
                functionSourceCode = node.text
                return functionSourceCode

    # Java Handler
    if language == 'java':
        if node.type == "constructor_declaration" \
                or node.type == "method_declaration" \
                or node.type == "class_declaration":
            identifier = getIdentifierInJava(node)
            if identifier == functionName.encode():
                functionSourceCode = node.text
                return functionSourceCode

    # Python Handler
    if language == 'python':
        if node.type == "class_definition" \
                or node.type == "function_definition":
            identifier = getIdentifierInPython(node)
            if identifier == functionName.encode():
                functionSourceCode = node.text
                return functionSourceCode

    for child in node.children:
        functionSourceCode = traverseTree(child, functionName, functionSourceCode, language)

    return functionSourceCode

def getFunctionSourceCode(functionName, language, souceCodeFilePath):
    assert language in languageMapping.keys(), language
    assert os.path.exists(souceCodeFilePath)

    LANGUAGE = languageMapping.get(language)
    parser = Parser()
    parser.set_language(LANGUAGE)

    with open(souceCodeFilePath, 'rb') as souceCodeFile:
        souceCode = souceCodeFile.read()

    if not functionName.encode() in souceCode:
        return None

    tree = parser.parse(souceCode)
    functionSourceCode = traverseTree(tree.root_node, functionName, None, language)

    return functionSourceCode



def handleOneJsonFile(jsonFile):
    jsonFileBaseName = os.path.basename(jsonFile)
    with open(jsonFile) as fp:
        jsonData = json.load(fp)

    # print(jsonData)
    projectZipFileName = jsonData["project_path"]
    projectZipFilePath = os.path.join(zipFilesPath, projectZipFileName)
    if not os.path.exists(projectZipFilePath):
        functionInfo = {
            **jsonData,
            'source_code': '',
            'question': jsonFileBaseName,
            'errMsg': 'Zip file not found'
        }
        return False, functionInfo

    projectZipFileNameHash = projectZipFileName.replace(".zip", "")
    unzippedProjectPath = os.path.join(trashPath, projectZipFileNameHash)
    makeEmpthDir(unzippedProjectPath)
    unzipProject(projectZipFilePath, unzippedProjectPath)
    setReadableWritableDir(unzippedProjectPath)

    LANGUAGE = jsonData['language'].lower()
    functionName = jsonData['function_name']

    souceCodeFile = jsonData['function_path']
    souceCodeFilePath = os.path.join(unzippedProjectPath, souceCodeFile)
    if not os.path.exists(souceCodeFilePath):
        souceCodeFilePathPattern = os.path.join(unzippedProjectPath, '*', souceCodeFile)
        souceCodeFilePathLists = glob.glob(souceCodeFilePathPattern)
        if not souceCodeFilePathLists:
            functionInfo = {
                **jsonData,
                'source_code': '',
                'question': jsonFileBaseName,
                'errMsg': 'Source code file not found.'
            }
            return False, functionInfo
        souceCodeFilePath = souceCodeFilePathLists[0]

    souceCode = getFunctionSourceCode(functionName, LANGUAGE, souceCodeFilePath)
    if souceCode is None:
        functionInfo = {
            **jsonData,
            'source_code': '',
            'question': jsonFileBaseName,
            'errMsg': 'Source code of function not found.'
        }
        return False, functionInfo

#==========================
    callTrace = getFucntionCallTrace(functionName, LANGUAGE, souceCodeFilePath, projectRootPath)
    functionInfo = {
        **jsonData,
        'source_code': souceCode.decode(),
        'question': jsonFileBaseName,
        'errMsg': ''
    }
    return True, functionInfo

if __name__ == '__main__':
    logger.info('Target: %s' % (jsonFilesPath))

    jsonFiles = findJsonFileByDir(jsonFilesPath)
    logger.info('Found ' + str(len(jsonFiles)) + ' JSON files')

    errorCount = 0
    errorInfoInOne = []
    functionInfoInOne = []
    # for jsonFile in tqdm.tqdm(jsonFiles):
    for idx, jsonFile in enumerate(jsonFiles):
        # if not jsonFile.endswith('0312545ee856018019ea44fcc5308dc8.json'):
        #     continue
        logger.info('[%d/%d]: %s' % (idx, len(jsonFiles), jsonFile))
        result, functionInfo = handleOneJsonFile(jsonFile)
        if result:
            functionInfoInOne.append(functionInfo)
        else:
            errorCount += 1
            logger.error('[%d]: %s' % (errorCount, functionInfo['errMsg']))
            errorInfoInOne.append(functionInfo)

    with open(saveTo, 'w') as fp:
        json.dump(functionInfoInOne, fp, indent=2)
    logger.info('Successfully saved %s function info.' % len(functionInfoInOne))
    logger.info('Successfully saved function info to file: %s.' % (saveTo))

    with open(errorTo, 'w') as fp:
        json.dump(errorInfoInOne, fp, indent=2)
    logger.info('Fails to save %d function info.' % len(errorInfoInOne))
    logger.info('Fails to save function info to file: %s.' % (errorTo))



