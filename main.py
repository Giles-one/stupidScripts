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
    level=logging.DEBUG,
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

souceCodeExtensionMapping = {
    'c/c++'  : ['.c', '.h', '.cpp', '.hpp', '.cc', '.cxx'],
    'python' : ['.py'],
    'java'   : ['.java']
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

def getFileListsWithExtension(rootPath, extensionLists):
    assert os.path.exists(rootPath)
    fileLists = []
    for root, dirs, files in os.walk(rootPath):
        for file in files:
            if not any(file.endswith(ext) for ext in extensionLists):
                continue
            filePath = os.path.join(root, file)
            fileLists.append(filePath)
    fileLists.sort()
    return fileLists

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
    nodeList = node.text.decode('utf-8', errors='ignore').split('\n')
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

def traverseForCalleeIdentifierInCAndCpp(node, calleeIdentifierLists):

    if node.type == "call_expression":
        for child in node.children:
            if child.type == "identifier":
                if not child.text in calleeIdentifierLists:
                    calleeIdentifierLists.append(child.text)
                break

    for child in node.children:
        calleeIdentifierLists = traverseForCalleeIdentifierInCAndCpp(child, calleeIdentifierLists)

    return calleeIdentifierLists

def traverseForCalleeIdentifierInJava(node, calleeIdentifierLists):
    if node.type == 'method_invocation':
        identifierLists = []
        for child in node.children:
            if child.type == 'identifier':
                identifierLists.append(child.text)
        if len(identifierLists) > 0 and (not identifierLists[-1] in calleeIdentifierLists):
            calleeIdentifierLists.append(identifierLists[-1])

    if node.type == 'object_creation_expression':
        for child in node.children:
            if child.type == 'type_identifier' and (not child.text in calleeIdentifierLists):
                calleeIdentifierLists.append(child.text)
                break

    for child in node.children:
        traverseForCalleeIdentifierInJava(child, calleeIdentifierLists)
    return calleeIdentifierLists

def traverseForCalleeNodeIdentifierInPython(node, identifierLists):
    if node.type == "identifier":
        identifierLists.append(node.text)

    for child in node.children:
        identifierLists = traverseForCalleeNodeIdentifierInPython(child, identifierLists)

    return identifierLists
def traverseForCalleeIdentifierInPython(node, calleeIdentifierLists):
    if node.type == "call":
        for child in node.children:
            if child.type == "identifier":
                if not child.text in calleeIdentifierLists:
                    calleeIdentifierLists.append(child.text)
                break
            if child.type == "attribute":
                identifierLists = []
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        identifierLists.append(grandchild.text)
                if len(identifierLists) > 0 and (not identifierLists[-1] in calleeIdentifierLists):
                    calleeIdentifierLists.append(identifierLists[-1])
                break
        return calleeIdentifierLists

    for child in node.children:
        calleeIdentifierLists = traverseForCalleeIdentifierInPython(child, calleeIdentifierLists)

    return calleeIdentifierLists


def traverseTreeForCallTrace(node, callTrace, language):

    if language == 'c/c++':
        if node.type == "function_definition":
            # watch point b'static void compile_xclass_matchingpath' in node.text
            identifier = getIdentifierInCAndCpp(node)
            if not identifier:
                identifier = getIdentifierByRe(node)
            if not identifier:
                return callTrace
            calleeIdentifierLists = traverseForCalleeIdentifierInCAndCpp(node, [])
            callTrace[identifier] = calleeIdentifierLists
    # Java Handler
    if language == 'java':
        if node.type == "constructor_declaration" \
                or node.type == "method_declaration" \
                or node.type == "class_declaration":
            identifier = getIdentifierInJava(node)
            if not identifier:
                return callTrace
            calleeIdentifierLists = traverseForCalleeIdentifierInJava(node, [])
            callTrace[identifier] = calleeIdentifierLists


    # Python Handler
    if language == 'python':
        if node.type == "class_definition" \
                or node.type == "function_definition":
            identifier = getIdentifierInPython(node)
            if not identifier:
                return callTrace
            calleeIdentifierLists = traverseForCalleeIdentifierInPython(node, [])
            callTrace[identifier] = calleeIdentifierLists

    for child in node.children:
        callTrace = traverseTreeForCallTrace(child, callTrace, language)

    return callTrace

def getOneFileFunctionCallTrace(functionName, language, souceCodeFilePath):
    assert language in languageMapping.keys(), language
    assert os.path.exists(souceCodeFilePath)

    LANGUAGE = languageMapping.get(language)
    parser = Parser()
    parser.set_language(LANGUAGE)

    with open(souceCodeFilePath, 'rb') as souceCodeFile:
        souceCode = souceCodeFile.read()

    # logger.debug(souceCodeFilePath)
    tree = parser.parse(souceCode)
    functionCallTrace = traverseTreeForCallTrace(tree.root_node, {}, language)

    return functionCallTrace


def getFunctionCallTraceOneRound(functionName, LANGUAGE, souceCodeFileLists):
    callerIdentifier = []
    validSourceCodeFileLists = []
    for filePath in souceCodeFileLists:
        with open(filePath, 'rb') as fp:
            sourceCode = fp.read()
        if functionName in sourceCode:
            validSourceCodeFileLists.append(filePath)

    for idx, sourceCodeFilePath in enumerate(validSourceCodeFileLists):
        # logger.debug('[%d]: %s '% (idx, sourceCodeFilePath))
        functionCallTrace = getOneFileFunctionCallTrace(functionName, LANGUAGE, sourceCodeFilePath)
        if not functionCallTrace:
            continue
        for caller, callees in functionCallTrace.items():
            if functionName in callees:
                callerIdentifier.append(caller)

    return callerIdentifier

def collectCallTrace(functionName, LANGUAGE, souceCodeFileLists, threshold):
    callerIdentifier = getFunctionCallTraceOneRound(functionName, LANGUAGE, souceCodeFileLists)
    if not threshold:
        return callerIdentifier

    # In order to reduce the order of magnitude of the algorithm,
    # limit each layer of the tree to no more than three nodes.
    callerIdentifier = callerIdentifier[:3]

    callTrace = []
    for callerFunctionName in callerIdentifier:
        callTrace.append(collectCallTrace(callerFunctionName, LANGUAGE, souceCodeFileLists, threshold - 1))

    return {functionName: callTrace}

def getFunctionCallTrace(functionName, LANGUAGE, projectRootPath, threshold):
    assert os.path.exists(projectRootPath)
    extensionLists = souceCodeExtensionMapping.get(LANGUAGE)
    souceCodeFileLists = getFileListsWithExtension(projectRootPath, extensionLists)

    functionName = functionName.encode()

    callTraceCollection = collectCallTrace(functionName, LANGUAGE, souceCodeFileLists, threshold)
    return callTraceCollection

def decodeTree(data):
    if isinstance(data, bytes):
        return data.decode('utf-8')
    elif isinstance(data, dict):
        return {decodeTree(key): decodeTree(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [decodeTree(element) for element in data]
    else:
        return data

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
    projectRootPath = unzippedProjectPath
    # if LANGUAGE == 'c/c++':
    errMsg = ''
    try:
        callTrace = getFunctionCallTrace(functionName, LANGUAGE, projectRootPath, threshold=3)
        callTrace = decodeTree(callTrace)
    except RecursionError:
        errMsg = 'RecursionError in get function call trace.'
        callTrace = {}

    # else:
    #     callTrace = {}
    #==========================

    functionInfo = {
        **jsonData,
        'source_code': souceCode.decode(),
        'question'   : jsonFileBaseName,
        'callTrace'  : callTrace,
        'errMsg'     : errMsg
    }
    return True, functionInfo

if __name__ == '__main__':
    logger.info('Target: %s' % (jsonFilesPath))

    jsonFiles = getFileListsWithExtension(jsonFilesPath, ['.json'])
    logger.info('Found ' + str(len(jsonFiles)) + ' JSON files')

    errorCount = 0
    errorInfoInOne = []
    functionInfoInOne = []
    # for jsonFile in tqdm.tqdm(jsonFiles):
    for idx, jsonFile in enumerate(jsonFiles):
        # if not jsonFile.endswith('c0b1092e623dc9ba0e3c9c2a4ca8e6dd.json'):
        #     continue
        # if idx <= 1624:
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



