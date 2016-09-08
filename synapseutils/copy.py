import synapseclient
from synapseclient import File, Project, Folder, Table, Schema, Link, Wiki, Activity, exceptions
import time
from synapseclient.exceptions import *
import tempfile
import re
############################################################
##                 Copy Functions                         ##
############################################################

def copy(syn, entity, destinationId=None, copyWikiPage=True, **kwargs):
    """
    - This function will assist users in copying entities (Tables, Links, Files, Folders, Projects),
      and will recursively copy everything in directories.
    - A Mapping of the old entities to the new entities will be created and all the wikis of each entity
      will also be copied over and links to synapse Ids will be updated.

    :param syn:             A synapse object: syn = synapseclient.login()- Must be logged into synapse

    :param entity:          A synapse entity ID

    :param destinationId:   Synapse ID of a folder/project that the copied entity is being copied to

    :param copyWikiPage:    Determines whether the wiki of the entity is copied over
                            Default is True

        import synapseutils
        import synapseclient
        syn = synapseclient.login()
        synapseutils.copy(syn, ...)

    # Examples and extra parameters unique to each copy function
    ### Copying Files ###

    :param version:         Can specify version of a file. 
                            Default to None

    :param update:          Can choose to update files that have the same name 
                            Default to False
    
    :param setProvenance:   Has three values to set the provenance of the copied entity:
                                traceback: Sets to the source entity
                                existing: Sets to source entity's original provenance (if it exists)
                                None: No provenance is set

        synapseutils.copy(syn, "syn12345", "syn45678", update=False, setProvenance = "traceback",version=None)

    ### Copying Folders/Projects ###

    :param excludeTypes:    Accepts a list of entity types (file, table, link) which determines which entity types to not copy.
                            Defaults to an empty list.

        #This will copy everything in the project into the destinationId except files and tables.
        synapseutils.copy(syn, "syn123450","syn345678",excludeTypes=["file","table"])

    Returns a mapping between the original and copied entity: {'syn1234':'syn33455'}
    """
    updateLinks = kwargs.get('updateLinks', True)
    updateSynIds = kwargs.get('updateSynIds', True)
    entitySubPageId = kwargs.get('entitySubPageId',None)
    destinationSubPageId = kwargs.get('destinationSubPageId',None)
    mapping = kwargs.pop('mapping',dict())

    mapping = _copyRecursive(syn, entity, destinationId, mapping = mapping, **kwargs)
    if copyWikiPage:
        for oldEnt in mapping:
            newWikig = copyWiki(syn, oldEnt, mapping[oldEnt], entitySubPageId = entitySubPageId,
                                destinationSubPageId = destinationSubPageId, updateLinks = updateLinks, 
                                updateSynIds = updateSynIds, entityMap = mapping)
    return(mapping)

def _copyRecursive(syn, entity, destinationId, mapping=dict(),**kwargs):
    """
    Recursively copies synapse entites, but does not copy the wikis

    :param entity:             A synapse entity ID

    :param destinationId:      Synapse ID of a folder/project that the copied entity is being copied to

    :param mapping:            Takes a mapping {'oldSynId': 'newSynId'} to help with replacing syn ids in the new wiki pages that aren't part of the entities being copied
                               Default to dict() and builds the mapping of all the synapse entities copied
    
    Returns a mapping between the original and copied entity: {'syn1234':'syn33455'}
    """

    version = kwargs.get('version', None)
    setProvenance = kwargs.get('setProvenance', "traceback")
    excludeTypes = kwargs.get('excludeTypes',[])
    update = kwargs.get('update',False)
    copiedId = None
    #Check that passed in excludeTypes is file, table, and link
    if not isinstance(excludeTypes,list):
        raise ValueError("Excluded types must be a list") 
    elif not all([i in ["file","link","table"] for i in excludeTypes]):
        raise ValueError("Excluded types can only be a list of these values: file, table, and link") 

    ent = syn.get(entity,downloadFile=False)
    if isinstance(ent, Project):
        if version is not None:
            raise ValueError("Cannot specify version when copying a project of folder")
        if not isinstance(syn.get(destinationId),Project):
            raise ValueError("You must give a destinationId of a new project to copy projects")
        copiedId = destinationId
        mapping[ent.id] = copiedId
        entities = syn.chunkedQuery('select id, name from entity where parentId=="%s"' % ent.id)
        for i in entities:
            mapping = _copyRecursive(syn, i['entity.id'], destinationId, mapping = mapping, **kwargs)
    elif isinstance(ent, Folder):
        if version is not None:
            raise ValueError("Cannot specify version when copying a project of folder")
        copiedId = _copyFolder(syn, ent.id, destinationId, mapping = mapping, **kwargs)
        mapping[ent.id] = copiedId
    elif isinstance(ent, File):
        if "file" not in excludeTypes:
            copiedId = _copyFile(syn, ent.id, destinationId, version = version, update = update, 
                                 setProvenance = setProvenance)
            mapping[ent.id] = copiedId
    elif isinstance(ent, Link):
        if "link" not in excludeTypes:
            copiedId = _copyLink(syn, ent.id, destinationId)
            #This is for copying Links, if the link target Id doesn't exist, copiedId will be None
            if copiedId is not None:
                mapping[ent.id] = copiedId
    elif isinstance(ent, Schema):
        if "table" not in excludeTypes:
            copiedId = _copyTable(syn, ent.id, destinationId)
            mapping[ent.id] = copiedId
    else:
        raise ValueError("Not able to copy this type of file")
    
    if copiedId is not None:
        print("Copied %s to %s" % (ent.id,copiedId))
    else:
        print("%s not copied" % ent.id)
    return(mapping)

def _copyFolder(syn, entity, destinationId, mapping=dict(), **kwargs):
    """
    Copies synapse folders

    :param entity:          A synapse ID of a Folder entity

    :param destinationId:   Synapse ID of a project/folder that the folder wants to be copied to
    
    :param excludeTypes:    Accepts a list of entity types (file, table, link) which determines which entity types to not copy.
                            Defaults to an empty list.
    """
    oldFolder = syn.get(entity)
    #CHECK: If Folder name already exists, raise value error
    search = syn.query('select name from entity where parentId == "%s"' % destinationId)
    for i in search['results']:
        if i['entity.name'] == oldFolder.name:
            raise ValueError('An item named "%s" already exists in this location. Folder could not be copied'%oldFolder.name)

    newFolder = Folder(name = oldFolder.name,parent= destinationId)
    newFolder.annotations = oldFolder.annotations
    newFolder = syn.store(newFolder)
    entities = syn.chunkedQuery('select id, name from entity where parentId=="%s"'% entity)
    for ent in entities:
        copied = _copyRecursive(syn, ent['entity.id'],newFolder.id,mapping, **kwargs)
    return(newFolder.id)

def _copyFile(syn, entity, destinationId, version=None, update=False, setProvenance="traceback"):
    """
    Copies most recent version of a file to a specified synapse ID.

    :param entity:          A synapse ID of a File entity

    :param destinationId:   Synapse ID of a folder/project that the file wants to be copied to

    :param version:         Can specify version of a file. 
                            Default to None

    :param update:          Can choose to update files that have the same name 
                            Default to False
    
    :param setProvenance:   Has three values to set the provenance of the copied entity:
                                traceback: Sets to the source entity
                                existing: Sets to source entity's original provenance (if it exists)
                                None: No provenance is set
    """
    ent = syn.get(entity, downloadFile=False, version=version, followLink=False)
    #CHECK: If File is in the same parent directory (throw an error) (Can choose to update files)
    if not update:
        search = syn.query('select name from entity where parentId =="%s"'%destinationId)
        for i in search['results']:
            if i['entity.name'] == ent.name:
                raise ValueError('An item named "%s" already exists in this location. File could not be copied'%ent.name)
    profile = syn.getUserProfile()
    # get provenance earlier to prevent errors from being called in the end
    # If traceback, set activity to old entity
    if setProvenance == "traceback":
        act = Activity("Copied file", used=ent)
    # if existing, check if provenance exists
    elif setProvenance == "existing":
        try:
            act = syn.getProvenance(ent.id)
        except SynapseHTTPError as e:
            # Should catch the 404
            act = None
    elif setProvenance is None or setProvenance.lower() == 'none':
        act = None
    else:
        raise ValueError('setProvenance must be one of None, existing, or traceback')
    #Grab file handle createdBy annotation to see the user that created fileHandle
    fileHandleList = syn.restGET('/entity/%s/version/%s/filehandles'%(ent.id,ent.versionNumber))
    #NOTE: May not always be the first index (need to filter to make sure not PreviewFileHandle)
    #Loop through to check which dataFileHandles match and return createdBy
    # Look at convenience function
    for fileHandle in fileHandleList['list']:
        if fileHandle['id'] == ent.dataFileHandleId:
            createdBy = fileHandle['createdBy']
            break
    else:
        createdBy = None
    #CHECK: If the user created the file, copy the file by using fileHandleId else hard copy
    if profile.ownerId == createdBy:
        new_ent = File(name=ent.name, parentId=destinationId)
        new_ent.dataFileHandleId = ent.dataFileHandleId
    else:
        #CHECK: If the synapse entity is an external URL, change path and store
        if ent.externalURL is None: #and ent.path == None:
            #####If you have never downloaded the file before, the path is None
            store = True
            #This needs to be here, because if the file has never been downloaded before
            #there wont be a ent.path
            ent = syn.get(entity,downloadFile=store,version=version)
            path = ent.path
        else:
            store = False
            ent = syn.get(entity,downloadFile=store,version=version)
            path = ent.externalURL

        new_ent = File(path, name=ent.name, parentId=destinationId, synapseStore=store)
    #Set annotations here
    new_ent.annotations = ent.annotations
    #Store provenance if act is not None
    if act is not None:
        new_ent = syn.store(new_ent, activity=act)
    else:
        new_ent = syn.store(new_ent)
    #Leave this return statement for test
    return new_ent['id']

def _copyTable(syn, entity, destinationId, setAnnotations=False):
    """
    Copies synapse Tables

    :param entity:          A synapse ID of Table Schema

    :param destinationId:   Synapse ID of a project that the Table wants to be copied to

    :param setAnnotations:  Set the annotations of the copied table to be the annotations of the entity
                            Defaults to False
    """

    print("Getting table %s" % entity)
    myTableSchema = syn.get(entity)
    #CHECK: If Table name already exists, raise value error
    search = syn.query('select name from table where projectId == "%s"' % destinationId)
    for i in search['results']:
        if i['table.name'] == myTableSchema.name:
            raise ValueError('A table named "%s" already exists in this location. Table could not be copied'%myTableSchema.name)

    d = syn.tableQuery('select * from %s' % myTableSchema.id)
    d = d.asDataFrame()
    d = d.reset_index()
    del d['index']

    colIds = myTableSchema.columnIds

    newTableSchema = Schema(name = myTableSchema.name,
                           parent = destinationId,
                           columns=colIds)
    if setAnnotations:
        newTableSchema.annotations = myTableSchema.annotations

    if len(d) > 0:
        print("Created new table using schema %s" % newTableSchema.name)
        newTable = Table(schema=newTableSchema,values=d)
        newTable = syn.store(newTable)
        return(newTable.schema.id)
    else:
        print("No data, so storing schema %s" % newTableSchema.name)
        newTableSchema = syn.store(newTableSchema)
        return(newTableSchema.id)

def _copyLink(syn, entity, destinationId):
    """
    Copies Link entities

    :param entity:          A synapse ID of a Link entity

    :param destinationId:   Synapse ID of a folder/project that the file wants to be copied to
    """
    ent = syn.get(entity)
    #CHECK: If Link is in the same parent directory (throw an error)
    search = syn.query('select name from entity where parentId =="%s"'%destinationId)
    for i in search['results']:
        if i['entity.name'] == ent.name:
            raise ValueError('An item named "%s" already exists in this location. Link could not be copied'%ent.name)

    newLink = Link(ent.linksTo['targetId'],parent=destinationId,targetVersion=ent.linksTo['targetVersionNumber'])
    try:
        newLink = syn.store(newLink)
        return(newLink.id)
    except Exception as e:
        print("WARNING: The target of this link %s no longer exists" % ent.id)
        return(None)

def _getSubWikiHeaders(wikiHeaders,subPageId,mapping=[]):
    #Function to assist in getting wiki headers of subwikipages
    subPageId = str(subPageId)
    for i in wikiHeaders:
        # This is for the first match 
        # If it isnt the actual parent, it will turn the first match into a parent node which will not have a parentId
        if i['id'] == subPageId and len(mapping) == 0:
            i.pop("parentId",None)
            mapping.append(i)
        #If a mapping already exists, it means that these pages have a parent node
        elif i['id'] == subPageId:
            mapping.append(i)
        #If parentId is not None, and if parent id is the subpage Id, pass it back into the function
        elif i.get('parentId',None) is not None:
            if i['parentId'] == subPageId:
                mapping = _getSubWikiHeaders(wikiHeaders,subPageId=i['id'],mapping=mapping)
    return(mapping)

def copyWiki(syn, entity, destinationId, entitySubPageId=None, destinationSubPageId=None, updateLinks=True, updateSynIds=True, entityMap=None):
    """
    Copies wikis and updates internal links

    :param syn:                     A synapse object: syn = synapseclient.login()- Must be logged into synapse

    :param entity:                  A synapse ID of an entity whose wiki you want to copy

    :param destinationId:           Synapse ID of a folder/project that the wiki wants to be copied to
    
    :param updateLinks:             Update all the internal links
                                    Defaults to True

    :param updateSynIds:            Update all the synapse ID's referenced in the wikis
                                    Defaults to True but needs an entityMap

    :param entityMap:               An entity map {'oldSynId','newSynId'} to update the synapse IDs referenced in the wiki
                                    Defaults to None 

    :param entitySubPageId:         Can specify subPageId and copy all of its subwikis
                                    Defaults to None, which copies the entire wiki
                                    subPageId can be found: https://www.synapse.org/#!Synapse:syn123/wiki/1234
                                    In this case, 1234 is the subPageId. 

    :param destinationSubPageId:    Can specify destination subPageId to copy wikis to
                                    Defaults to None
    """
    oldOwn = syn.get(entity,downloadFile=False)
    # getWikiHeaders fails when there is no wiki
    try:
        oldWh = syn.getWikiHeaders(oldOwn)
        store = True
    except SynapseHTTPError:
        store = False
    if store:
        if entitySubPageId is not None:
            oldWh = _getSubWikiHeaders(oldWh,entitySubPageId,mapping=[])
        newOwn =syn.get(destinationId,downloadFile=False)
        wikiIdMap =dict()
        newWikis=dict()
        for i in oldWh:
            attDir=tempfile.NamedTemporaryFile(prefix='attdir',suffix='')
            #print i['id']
            wiki = syn.getWiki(oldOwn, i.id)
            print('Got wiki %s' % i.id)
            if wiki['attachmentFileHandleIds'] == []:
                attachments = []
            elif wiki['attachmentFileHandleIds'] != []:
                uri = "/entity/%s/wiki/%s/attachmenthandles" % (wiki.ownerId, wiki.id)
                results = syn.restGET(uri)
                file_handles = {fh['id']:fh for fh in results['list']}
                ## need to download an re-upload wiki attachments, ug!
                attachments = []
                tempdir = tempfile.gettempdir()
                for fhid in wiki.attachmentFileHandleIds:
                    file_info = syn._downloadWikiAttachment(wiki.ownerId, wiki, file_handles[fhid]['fileName'], destination=tempdir)
                    attachments.append(file_info['path'])
            #for some reason some wikis don't have titles?
            if hasattr(i, 'parentId'):
                wNew = Wiki(owner=newOwn, title=wiki.get('title',''), markdown=wiki.markdown, attachments=attachments, parentWikiId=wikiIdMap[wiki.parentWikiId])
                wNew = syn.store(wNew)
            else:
                if destinationSubPageId is not None:
                    wNew = syn.getWiki(newOwn, destinationSubPageId)
                    wNew.attachments = attachments
                    wNew.markdown = wiki.markdown
                    #Need to add logic to update titles here
                    wNew = syn.store(wNew)
                else:
                    wNew = Wiki(owner=newOwn, title=wiki.get('title',''), markdown=wiki.markdown, attachments=attachments, parentWikiId=destinationSubPageId)
                    wNew = syn.store(wNew)
            newWikis[wNew.id]=wNew
            wikiIdMap[wiki.id] =wNew.id

        if updateLinks:
            print("Updating internal links:\n")
            for oldWikiId in wikiIdMap.keys():
                # go through each wiki page once more:
                newWikiId=wikiIdMap[oldWikiId]
                newWiki=newWikis[newWikiId]
                print("\tUpdating internal links for Page: %s\n" % newWikiId)
                s=newWiki.markdown
                # in the markdown field, replace all occurrences of entity/wiki/abc with destinationId/wiki/xyz,
                # where wikiIdMap maps abc->xyz
                # replace <entity>/wiki/<oldWikiId> with <destinationId>/wiki/<newWikiId> 
                for oldWikiId2 in wikiIdMap.keys():
                    oldProjectAndWikiId = "%s/wiki/%s" % (entity, oldWikiId2)
                    newProjectAndWikiId = "%s/wiki/%s" % (destinationId, wikiIdMap[oldWikiId2])
                    s=re.sub(oldProjectAndWikiId, newProjectAndWikiId, s)
                # now replace any last references to entity with destinationId
                s=re.sub(entity, destinationId, s)
                newWikis[newWikiId].markdown=s

        if updateSynIds and entityMap is not None:
            print("Updating Synapse references:\n")
            for oldWikiId in wikiIdMap.keys():
                # go through each wiki page once more:
                newWikiId = wikiIdMap[oldWikiId]
                newWiki = newWikis[newWikiId]
                print('Updated Synapse references for Page: %s\n' %newWikiId)
                s = newWiki.markdown

                for oldSynId in entityMap.keys():
                    # go through each wiki page once more:
                    newSynId = entityMap[oldSynId]
                    s = re.sub(oldSynId, newSynId, s)
                print("Done updating Synpase IDs.\n")
                newWikis[newWikiId].markdown = s
        
        print("Storing new Wikis\n")
        for oldWikiId in wikiIdMap.keys():
            newWikiId = wikiIdMap[oldWikiId]
            newWikis[newWikiId] = syn.store(newWikis[newWikiId])
            print("\tStored: %s\n" % newWikiId)
        newWh = syn.getWikiHeaders(newOwn)
        return(newWh)
    else:
        return("no wiki")
